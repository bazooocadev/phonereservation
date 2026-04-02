"""
リダイアルエンジン — メインの非同期発信・転送制御ロジック

フロー:
1. 宛先ごと・回線ごとに独立したループで常時発信し続ける
2. いずれかの回線が接続 → 他の回線をキャンセル → 担当者へ転送
3. 接続通話が終了 → 全回線ループを再開
4. WebSocket でリアルタイム状態を配信
"""
import asyncio
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.destination import Destination
from app.models.carrier_line import CarrierLine
from app.models.operator import Operator
from app.models.call_log import CallLog
from app.config import get_settings
from app.services import websocket_manager

logger = logging.getLogger(__name__)
settings = get_settings()


class DestState:
    """宛先ごとの接続状態（全回線ループで共有）"""
    def __init__(self, dest_id: int):
        self.dest_id = dest_id
        self.connected = False
        self.connected_call_sid: Optional[str] = None
        self.connected_event = asyncio.Event()   # いずれかの回線が接続確定
        self.call_ended_event = asyncio.Event()  # 接続通話が終了


class ActiveCall:
    """発信中通話の状態保持"""
    def __init__(self, call_sid: str, carrier: str, dest_id: int, log_id: int,
                 is_conference: bool = False, conference_name: Optional[str] = None,
                 from_number: str = ""):
        self.call_sid = call_sid
        self.carrier = carrier
        self.dest_id = dest_id
        self.log_id = log_id
        self.is_conference = is_conference
        self.conference_name = conference_name
        self.from_number = from_number
        self.status = "ringing"
        self.result_event = asyncio.Event()  # 通話結果が確定（busy/failed/connected等）
        self.result: Optional[str] = None


class RedialEngine:
    def __init__(self):
        self.is_running = False
        self.dial_interval_sec: float = settings.dial_interval_sec
        self._tasks: list[asyncio.Task] = []
        self._active_calls: Dict[str, ActiveCall] = {}
        self._operator_calls: Dict[int, str] = {}
        self._dest_states: Dict[int, DestState] = {}
        self._pre_ended_calls: set[str] = set()

    async def _sync_settings(self):
        from app.models.system_setting import SystemSetting
        async with AsyncSessionLocal() as db:
            setting = await db.get(SystemSetting, 1)
            if setting:
                self.dial_interval_sec = setting.dial_interval_sec

    @property
    def active_call_count(self) -> int:
        return len(self._active_calls)

    async def start(self):
        if self.is_running:
            logger.info("Engine already running")
            return
        self.is_running = True
        await self._sync_settings()
        logger.info("Redial engine starting...")
        async with AsyncSessionLocal() as db:
            dests = (await db.execute(
                select(Destination).where(Destination.is_active == True)
            )).scalars().all()

        for dest in dests:
            task = asyncio.create_task(self._dest_loop(dest.id))
            self._tasks.append(task)

        await websocket_manager.broadcast({"type": "engine", "running": True})
        logger.info(f"Engine started with {len(self._tasks)} destination loops")

    async def stop(self):
        self.is_running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._active_calls:
            hangup_coros = [
                self._do_hangup(call_sid, ac.carrier)
                for call_sid, ac in self._active_calls.items()
            ]
            try:
                await asyncio.wait_for(
                    asyncio.gather(*hangup_coros, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Hangup timed out during stop, proceeding anyway")

        self._active_calls.clear()
        self._dest_states.clear()
        self._pre_ended_calls.clear()

        for op_id in list(self._operator_calls.keys()):
            async with AsyncSessionLocal() as db:
                op = await db.get(Operator, op_id)
                if op:
                    op.is_available = True
                    await db.commit()
        self._operator_calls.clear()

        await websocket_manager.broadcast({"type": "engine", "running": False})
        logger.info("Redial engine stopped")

    async def hangup_operator(self, operator_id: int):
        """担当者への転送通話を強制切断"""
        call_sid = self._operator_calls.get(operator_id)
        if not call_sid:
            return
        ac = self._active_calls.get(call_sid)
        if ac:
            await self._do_hangup(call_sid, ac.carrier)
        self._operator_calls.pop(operator_id, None)

    async def _dest_loop(self, dest_id: int):
        """宛先1件ごとのループ。回線ごとのタスクを管理する。"""
        while self.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    dest = await db.get(Destination, dest_id)
                    if not dest or not dest.is_active:
                        await asyncio.sleep(5)
                        continue

                    lines_result = await db.execute(
                        select(CarrierLine)
                        .where(CarrierLine.destination_id == dest_id)
                        .where(CarrierLine.is_active == True)
                        .order_by(CarrierLine.priority)
                        .limit(dest.allocated_lines)
                    )
                    lines = lines_result.scalars().all()

                if not lines:
                    await asyncio.sleep(5)
                    continue

                # 宛先状態を初期化
                dest_state = DestState(dest_id)
                self._dest_states[dest_id] = dest_state

                # 回線ごとの独立ループを起動
                line_tasks = [
                    asyncio.create_task(self._line_loop(dest, line, dest_state))
                    for line in lines
                ]

                # いずれかが接続するまで待機
                try:
                    await asyncio.wait_for(dest_state.connected_event.wait(), timeout=86400)
                except asyncio.TimeoutError:
                    pass

                # 全回線ループをキャンセル（キャンセル時に各ループが進行中の通話をハングアップ）
                for t in line_tasks:
                    t.cancel()
                await asyncio.gather(*line_tasks, return_exceptions=True)

                # 接続通話が終了するまで待機してから次のサイクルへ
                if dest_state.connected:
                    try:
                        await asyncio.wait_for(dest_state.call_ended_event.wait(), timeout=3600)
                    except asyncio.TimeoutError:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"dest_loop error (dest={dest_id}): {e}", exc_info=True)
                await asyncio.sleep(5)
            finally:
                self._dest_states.pop(dest_id, None)

    async def _line_loop(self, dest: Destination, line: CarrierLine, dest_state: DestState):
        """回線1本ごとの独立ループ。接続確定まで発信し続ける。"""
        while self.is_running and not dest_state.connected_event.is_set():
            call_sid = await self._make_line_call(dest, line)

            if not call_sid:
                # 発信失敗（Twilio APIエラー等）→ 少し待ってリトライ
                try:
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    return
                continue

            # 通話結果を待つ（webhook が result_event をセットする）
            ac = self._active_calls.get(call_sid)
            if not ac:
                continue

            try:
                await asyncio.wait_for(ac.result_event.wait(), timeout=120)
            except asyncio.TimeoutError:
                logger.warning(f"line_loop: result_event timeout for {call_sid}")
                await self._do_hangup(call_sid, line.carrier)
                continue
            except asyncio.CancelledError:
                # _dest_loop からキャンセル → 進行中の通話をハングアップ
                if call_sid in self._active_calls:
                    await self._do_hangup(call_sid, line.carrier)
                raise

            result = ac.result
            if result == "connected":
                # 接続確定 → ループ終了（_dest_loop が call_ended_event を待つ）
                break
            # busy / failed / no-answer / congested / cancelled → 即再発信

    async def _make_line_call(self, dest: Destination, line: CarrierLine) -> Optional[str]:
        """1件の発信を行い call_sid を返す。失敗時は None。"""
        async with AsyncSessionLocal() as db:
            is_conference = dest.transfer_on_ringing
            conference_name = f"conf_{uuid.uuid4().hex[:12]}" if is_conference else None

            log = CallLog(
                destination_id=dest.id,
                carrier_line_id=line.id,
                carrier=line.carrier,
                from_number=line.from_number,
                to_number=dest.phone_number,
                called_at=datetime.now(timezone.utc),
                result="calling",
                conference_name=conference_name,
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id

        call_sid: Optional[str] = None
        try:
            if not self.is_running:
                async with AsyncSessionLocal() as db:
                    log_upd = await db.get(CallLog, log_id)
                    if log_upd:
                        log_upd.result = "cancelled"
                        log_upd.ended_at = datetime.now(timezone.utc)
                        await db.commit()
                return None

            loop = asyncio.get_event_loop()
            if line.carrier == "twilio":
                from app.services.carrier import twilio_client
                if is_conference:
                    call_sid = await loop.run_in_executor(
                        None,
                        twilio_client.make_call_conference,
                        line.from_number,
                        dest.phone_number,
                        conference_name,
                        settings.webhook_base_url,
                    )
                else:
                    call_sid = await loop.run_in_executor(
                        None,
                        twilio_client.make_call,
                        line.from_number,
                        dest.phone_number,
                        settings.webhook_base_url,
                    )
            else:
                raise RuntimeError(f"Telnyx is currently disabled. carrier={line.carrier}")

            if not call_sid:
                return None

            if not self.is_running:
                asyncio.create_task(self._do_hangup(call_sid, line.carrier))
                return None

            if call_sid in self._pre_ended_calls:
                self._pre_ended_calls.discard(call_sid)
                asyncio.create_task(self._do_hangup(call_sid, line.carrier))
                return None

            self._active_calls[call_sid] = ActiveCall(
                call_sid, line.carrier, dest.id, log_id,
                is_conference, conference_name,
                from_number=line.from_number,
            )

            async with AsyncSessionLocal() as db:
                log_upd = await db.get(CallLog, log_id)
                if log_upd:
                    log_upd.call_sid = call_sid
                    await db.commit()

            await websocket_manager.broadcast({
                "type": "call_started",
                "call_sid": call_sid,
                "carrier": line.carrier,
                "dest_name": dest.name,
                "to": dest.phone_number,
            })

            return call_sid

        except Exception as e:
            logger.error(f"Call failed ({line.carrier}): {e}")
            async with AsyncSessionLocal() as db:
                log_upd = await db.get(CallLog, log_id)
                if log_upd:
                    log_upd.result = "failed"
                    log_upd.ended_at = datetime.now(timezone.utc)
                    await db.commit()
            return None

    async def _on_dest_connected(self, call_sid: str):
        """
        いずれかの回線が接続確定 → 他の通話をキャンセルして dest_state を更新。
        二重呼び出し防止のため connected フラグで排他制御。
        """
        ac = self._active_calls.get(call_sid)
        if not ac:
            return

        dest_state = self._dest_states.get(ac.dest_id)
        if not dest_state or dest_state.connected:
            return

        dest_state.connected = True
        dest_state.connected_call_sid = call_sid
        dest_state.connected_event.set()

        # 同じ宛先の他の通話をハングアップ＆result_event をセット
        for sid, other_ac in list(self._active_calls.items()):
            if sid != call_sid and other_ac.dest_id == ac.dest_id:
                asyncio.create_task(self._do_hangup(sid, other_ac.carrier))
                other_ac.result = "cancelled"
                other_ac.result_event.set()

    async def on_call_ringing(self, call_sid: str):
        """宛先呼び出し音(ringing)検知時（コンファレンスモード専用）。"""
        ac = self._active_calls.get(call_sid)
        if not ac or not ac.is_conference:
            return
        # コンファレンスモードはringing時点で接続確定扱い（他回線キャンセル）
        await self._on_dest_connected(call_sid)

    async def on_call_answered(self, call_sid: str):
        """通話確立時。担当者へ転送する。"""
        ac = self._active_calls.get(call_sid)
        if not ac:
            return

        if ac.is_conference:
            # コンファレンスモード: 宛先が応答したので担当者をコンファレンスへ招待
            async with AsyncSessionLocal() as db:
                log = await db.get(CallLog, ac.log_id)
                if log:
                    log.result = "connected"
                    log.connected_at = datetime.now(timezone.utc)
                    await db.commit()

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Operator)
                    .where(Operator.is_active == True)
                    .where(Operator.is_available == True)
                    .order_by(Operator.priority)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                operator = result.scalar_one_or_none()

                if not operator:
                    logger.warning(f"No available operator for answered conference call {call_sid}.")
                    return

                operator.is_available = False
                log = await db.get(CallLog, ac.log_id)
                if log:
                    log.operator_id = operator.id
                    log.transfer_to = operator.phone_number
                await db.commit()

            loop = asyncio.get_event_loop()
            self._operator_calls[operator.id] = call_sid
            try:
                from app.services.carrier import twilio_client
                if not ac.from_number:
                    logger.error("from_number not found for conference call")
                    self._operator_calls.pop(operator.id, None)
                    async with AsyncSessionLocal() as db:
                        op = await db.get(Operator, operator.id)
                        if op:
                            op.is_available = True
                            await db.commit()
                    return

                op_call_sid = await loop.run_in_executor(
                    None,
                    twilio_client.call_operator_to_conference,
                    ac.from_number,
                    operator.phone_number,
                    ac.conference_name,
                    settings.webhook_base_url,
                )

                async with AsyncSessionLocal() as db:
                    log_upd = await db.get(CallLog, ac.log_id)
                    if log_upd:
                        log_upd.operator_call_sid = op_call_sid
                        await db.commit()

                await websocket_manager.broadcast({
                    "type": "call_transferred",
                    "call_sid": call_sid,
                    "operator_name": operator.name,
                    "note": "conference_mode",
                })

            except Exception as e:
                logger.error(f"Conference operator call failed: {e}")
                self._operator_calls.pop(operator.id, None)
                async with AsyncSessionLocal() as db:
                    op = await db.get(Operator, operator.id)
                    if op:
                        op.is_available = True
                        await db.commit()

            # result_event をセット（_line_loop に接続を通知）
            ac.result = "connected"
            ac.result_event.set()
            return

        # 通常モード: 他回線キャンセル＋担当者転送
        await self._on_dest_connected(call_sid)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Operator)
                .where(Operator.is_active == True)
                .where(Operator.is_available == True)
                .order_by(Operator.priority)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            operator = result.scalar_one_or_none()

            if not operator:
                logger.warning(f"No available operator for {call_sid}.")
                return

            operator.is_available = False
            await db.commit()

            log = await db.get(CallLog, ac.log_id)
            if log:
                log.result = "connected"
                log.connected_at = datetime.now(timezone.utc)
                log.operator_id = operator.id
                log.transfer_to = operator.phone_number
                await db.commit()

        loop = asyncio.get_event_loop()
        self._operator_calls[operator.id] = call_sid
        try:
            if ac.carrier == "twilio":
                from app.services.carrier import twilio_client
                await loop.run_in_executor(
                    None,
                    twilio_client.transfer_call,
                    call_sid,
                    operator.phone_number,
                    settings.webhook_base_url,
                )
            else:
                raise RuntimeError(f"Telnyx is currently disabled. carrier={ac.carrier}")
            await websocket_manager.broadcast({
                "type": "call_transferred",
                "call_sid": call_sid,
                "operator_name": operator.name,
            })
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            self._operator_calls.pop(operator.id, None)
            async with AsyncSessionLocal() as db:
                op = await db.get(Operator, operator.id)
                if op:
                    op.is_available = True
                    await db.commit()

        # result_event をセット（_line_loop に接続を通知）
        ac.result = "connected"
        ac.result_event.set()

    async def on_call_ended(self, call_sid: str, result: str, duration_sec: int = 0):
        """通話終了時のクリーンアップ"""
        ac = self._active_calls.pop(call_sid, None)
        if not ac:
            self._pre_ended_calls.add(call_sid)
            return

        # 接続通話が終了した場合 → call_ended_event をセットして次サイクルへ
        dest_state = self._dest_states.get(ac.dest_id)
        if dest_state and dest_state.connected and call_sid == dest_state.connected_call_sid:
            dest_state.call_ended_event.set()
        else:
            # 未接続回線の終了 → result_event をセットして _line_loop に通知
            ac.result = result
            ac.result_event.set()

        # 担当者を空き状態に戻す
        for op_id, sid in list(self._operator_calls.items()):
            if sid == call_sid:
                self._operator_calls.pop(op_id, None)
                async with AsyncSessionLocal() as db:
                    op = await db.get(Operator, op_id)
                    if op:
                        op.is_available = True
                        await db.commit()
                    if result not in ("connected", "cancelled"):
                        log = await db.get(CallLog, ac.log_id)
                        if log and log.operator_call_sid:
                            await self._do_hangup(log.operator_call_sid, ac.carrier)
                break

        await websocket_manager.broadcast({
            "type": "call_ended",
            "call_sid": call_sid,
            "result": result,
            "duration_sec": duration_sec,
        })

    async def _do_hangup(self, call_sid: str, carrier: str):
        loop = asyncio.get_event_loop()
        try:
            if carrier == "twilio":
                from app.services.carrier import twilio_client
                await loop.run_in_executor(None, twilio_client.hangup_call, call_sid)
            else:
                raise RuntimeError(f"Telnyx is currently disabled. carrier={carrier}")
        except Exception as e:
            logger.warning(f"Hangup error: {e}")


# シングルトン
engine = RedialEngine()
