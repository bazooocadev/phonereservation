"""
リダイアルエンジン — メインの非同期発信・転送制御ロジック

フロー:
1. 宛先ごとに割当回線数を同時並列発信（バッチ）
2. いずれかの回線が接続 → 他の回線をキャンセル → 担当者へ転送
3. すべて失敗（混雑/話中/応答なし）→ リダイアル間隔後に再バッチ発信
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


class CallBatch:
    """順次発信バッチ（1サイクル分）"""
    def __init__(self, batch_id: str, dest_id: int):
        self.batch_id = batch_id
        self.dest_id = dest_id
        self.call_sids: set[str] = set()
        self.connected = False
        self.connected_call_sid: Optional[str] = None
        self.done_event = asyncio.Event()       # 接続確定 or 全失敗
        self.call_ended_event = asyncio.Event() # 接続した通話が終了
        self.all_dialed = False                 # 全回線のダイアル試行が完了したか


class ActiveCall:
    """発信中通話の状態保持"""
    def __init__(self, call_sid: str, carrier: str, dest_id: int, log_id: int,
                 is_conference: bool = False, conference_name: Optional[str] = None,
                 from_number: str = "", batch_id: str = ""):
        self.call_sid = call_sid
        self.carrier = carrier
        self.dest_id = dest_id
        self.log_id = log_id
        self.is_conference = is_conference
        self.conference_name = conference_name
        self.from_number = from_number
        self.batch_id = batch_id
        self.status = "ringing"  # ringing / connected / transferring


class RedialEngine:
    def __init__(self):
        self.is_running = False
        self._tasks: list[asyncio.Task] = []
        self._active_calls: Dict[str, ActiveCall] = {}
        self._operator_calls: Dict[int, str] = {}
        self._batches: Dict[str, CallBatch] = {}

    @property
    def active_call_count(self) -> int:
        return len(self._active_calls)

    async def start(self):
        if self.is_running:
            logger.info("Engine already running")
            return
        self.is_running = True
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

        # アクティブな通話をすべてハングアップ（停止後も電話がかかり続けるのを防ぐ）
        for call_sid, ac in list(self._active_calls.items()):
            asyncio.create_task(self._do_hangup(call_sid, ac.carrier))
        self._active_calls.clear()
        self._batches.clear()

        # 担当者を空き状態に戻す
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
        """宛先1件ごとのリダイアルループ（バッチ並列発信）"""
        while self.is_running:
            batch = None
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

                # バッチ作成 → 1回線ずつ順次発信
                batch_id = uuid.uuid4().hex[:12]
                batch = CallBatch(batch_id, dest_id)
                self._batches[batch_id] = batch

                # 1回線ずつ順番にダイアル（dial_interval_sec 間隔）
                for i, line in enumerate(lines):
                    if not self.is_running or batch.connected or batch.done_event.is_set():
                        break
                    asyncio.create_task(self._make_single_call(dest, line, batch_id))
                    # 最後の回線以外は interval 秒待機（その間に接続すればループを抜ける）
                    if i < len(lines) - 1:
                        try:
                            await asyncio.wait_for(
                                batch.done_event.wait(),
                                timeout=settings.dial_interval_sec,
                            )
                        except asyncio.TimeoutError:
                            pass

                # 全回線のダイアル試行完了を記録し、残りのバッチ完了を待機
                batch.all_dialed = True
                await self._check_batch_all_failed(batch.batch_id)

                if not batch.done_event.is_set():
                    try:
                        await asyncio.wait_for(batch.done_event.wait(), timeout=60)
                    except asyncio.TimeoutError:
                        logger.warning(f"Batch {batch_id} timed out for dest {dest_id}")

                if batch.connected:
                    # 接続した通話が終了するまで待機してから次のサイクルへ
                    try:
                        await asyncio.wait_for(batch.call_ended_event.wait(), timeout=3600)
                    except asyncio.TimeoutError:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"dest_loop error (dest={dest_id}): {e}", exc_info=True)
            finally:
                if batch:
                    self._batches.pop(batch.batch_id, None)

            # 全失敗時のみリダイアル間隔を挟む（接続時はすぐ次へ）
            if self.is_running and (batch is None or not batch.connected):
                await asyncio.sleep(settings.redial_interval_sec)

    async def _make_single_call(self, dest: Destination, line: CarrierLine, batch_id: str = ""):
        """1件の発信を行いバッチに登録する"""
        call_sid: Optional[str] = None

        async with AsyncSessionLocal() as db:
            is_conference = dest.transfer_on_ringing
            conference_name = None
            if is_conference:
                conference_name = f"conf_{uuid.uuid4().hex[:12]}"

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

        try:
            # エンジン停止済みなら発信しない（Twilio API 呼び出し前にチェック）
            if not self.is_running:
                async with AsyncSessionLocal() as db:
                    log_upd = await db.get(CallLog, log_id)
                    if log_upd:
                        log_upd.result = "cancelled"
                        log_upd.ended_at = datetime.now(timezone.utc)
                        await db.commit()
                return

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

            if call_sid:
                # エンジン停止済みなら即ハングアップして終了
                if not self.is_running:
                    asyncio.create_task(self._do_hangup(call_sid, line.carrier))
                    return

                # _active_calls を先に登録（ringing コールバックとの競合を防ぐ）
                self._active_calls[call_sid] = ActiveCall(
                    call_sid, line.carrier, dest.id, log_id,
                    is_conference, conference_name,
                    from_number=line.from_number,
                    batch_id=batch_id,
                )
                # バッチに登録
                if batch_id:
                    batch = self._batches.get(batch_id)
                    if batch:
                        batch.call_sids.add(call_sid)

                # call_sid を DB に保存
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

        except Exception as e:
            logger.error(f"Call failed ({line.carrier}): {e}")
            async with AsyncSessionLocal() as db:
                log_upd = await db.get(CallLog, log_id)
                if log_upd:
                    log_upd.result = "failed"
                    log_upd.ended_at = datetime.now(timezone.utc)
                    await db.commit()
            # 発信失敗もバッチの完了判定に含める
            if batch_id:
                await self._check_batch_all_failed(batch_id)

    async def _on_batch_connected(self, call_sid: str):
        """
        バッチ内のいずれかが接続確定 → 他の通話をキャンセルしてバッチを完了とする。
        二重呼び出し防止のため connected フラグで排他制御。
        """
        ac = self._active_calls.get(call_sid)
        if not ac or not ac.batch_id:
            return

        batch = self._batches.get(ac.batch_id)
        if not batch or batch.connected:
            return  # すでに別の回線が接続済み

        batch.connected = True
        batch.connected_call_sid = call_sid

        # 同バッチの他の通話をハングアップ
        for other_sid in list(batch.call_sids):
            if other_sid != call_sid:
                other_ac = self._active_calls.get(other_sid)
                if other_ac:
                    asyncio.create_task(self._do_hangup(other_sid, other_ac.carrier))

        batch.done_event.set()

    async def _check_batch_all_failed(self, batch_id: str):
        """バッチ内の全通話が終了かつ未接続なら done_event をセットする"""
        batch = self._batches.get(batch_id)
        if not batch or batch.connected or batch.done_event.is_set():
            return
        # 全回線のダイアル試行完了 かつ call_sids が空（全通話終了）になったらリダイアルへ
        # all_dialed が False の間は、まだ未ダイアルの回線が残っているので待機
        if batch.all_dialed and not batch.call_sids:
            batch.done_event.set()

    async def on_call_ringing(self, call_sid: str):
        """
        宛先呼び出し音(ringing)検知時（コンファレンスモード専用）。
        バッチの他回線をキャンセルし、担当者をコンファレンスへ招待する。
        """
        ac = self._active_calls.get(call_sid)
        if not ac or not ac.is_conference:
            return

        # 最初にringingになった回線でバッチを確定（他をキャンセル）
        await self._on_batch_connected(call_sid)

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
                logger.warning(f"No available operator for ringing call {call_sid}.")
                return

            operator.is_available = False
            log = await db.get(CallLog, ac.log_id)
            if log:
                log.operator_id = operator.id
                log.transfer_to = operator.phone_number
            await db.commit()

        loop = asyncio.get_event_loop()
        try:
            from app.services.carrier import twilio_client
            from_number = ac.from_number
            if not from_number:
                logger.error("from_number not found for conference call")
                return

            op_call_sid = await loop.run_in_executor(
                None,
                twilio_client.call_operator_to_conference,
                from_number,
                operator.phone_number,
                ac.conference_name,
                settings.webhook_base_url,
            )

            async with AsyncSessionLocal() as db:
                log_upd = await db.get(CallLog, ac.log_id)
                if log_upd:
                    log_upd.operator_call_sid = op_call_sid
                    await db.commit()

            self._operator_calls[operator.id] = call_sid
            await websocket_manager.broadcast({
                "type": "call_transferred",
                "call_sid": call_sid,
                "operator_name": operator.name,
                "note": "ringing_mode",
            })

        except Exception as e:
            logger.error(f"Conference operator call failed: {e}")
            async with AsyncSessionLocal() as db:
                op = await db.get(Operator, operator.id)
                if op:
                    op.is_available = True
                    await db.commit()

    async def on_call_answered(self, call_sid: str):
        """
        通話確立時（通常モード）。バッチの他回線をキャンセルし担当者へ転送する。
        """
        ac = self._active_calls.get(call_sid)
        if not ac:
            return

        if ac.is_conference:
            # コンファレンスモードは on_call_ringing で処理済み（ログ時刻のみ更新）
            async with AsyncSessionLocal() as db:
                log = await db.get(CallLog, ac.log_id)
                if log:
                    log.result = "connected"
                    log.connected_at = datetime.now(timezone.utc)
                    await db.commit()
            return

        # 通常モード: バッチの他回線をキャンセル
        await self._on_batch_connected(call_sid)

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
        except Exception as e:
            logger.error(f"Transfer failed: {e}")

        self._operator_calls[operator.id] = call_sid
        await websocket_manager.broadcast({
            "type": "call_transferred",
            "call_sid": call_sid,
            "operator_name": operator.name,
        })

    async def on_call_ended(self, call_sid: str, result: str, duration_sec: int = 0):
        """通話終了時のクリーンアップ"""
        ac = self._active_calls.pop(call_sid, None)
        if not ac:
            return

        # バッチの完了判定
        if ac.batch_id:
            batch = self._batches.get(ac.batch_id)
            if batch:
                batch.call_sids.discard(call_sid)
                if batch.connected and call_sid == batch.connected_call_sid:
                    # 接続していた通話が終了 → 次のサイクルへ
                    batch.call_ended_event.set()
                elif not batch.connected and not batch.call_sids:
                    # 全通話終了かつ未接続 → リダイアルへ
                    batch.done_event.set()

        # 担当者を空き状態に戻す
        for op_id, sid in list(self._operator_calls.items()):
            if sid == call_sid:
                self._operator_calls.pop(op_id, None)
                async with AsyncSessionLocal() as db:
                    op = await db.get(Operator, op_id)
                    if op:
                        op.is_available = True
                        await db.commit()
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
