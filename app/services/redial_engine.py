"""
リダイアルエンジン — メインの非同期発信・転送制御ロジック

フロー:
1. 設定された宛先ごとに割り当て回線数を並列発信
2. キャリア優先順位（Twilio優先 → Telnyx フォールバック）
3. 通話確立後 → 空き担当者スマホへ転送
4. 混雑/話中/失敗 → リダイアル間隔後に再発信
5. WebSocket でリアルタイム状態を配信
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
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
        self.status = "ringing"   # ringing / connected / transferring


class RedialEngine:
    def __init__(self):
        self.is_running = False
        self._tasks: list[asyncio.Task] = []
        # call_sid -> ActiveCall
        self._active_calls: Dict[str, ActiveCall] = {}
        # operator_id -> call_sid
        self._operator_calls: Dict[int, str] = {}

    @property
    def active_call_count(self) -> int:
        return len(self._active_calls)

    async def start(self):
        if self.is_running:
            logger.info("Engine already running")
            return
        self.is_running = True
        logger.info("Redial engine starting...")
        # 宛先ごとにタスクを起動
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
        self._tasks.clear()
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
        """宛先1件ごとのリダイアルループ"""
        while self.is_running:
            try:
                async with AsyncSessionLocal() as db:
                    dest = await db.get(Destination, dest_id)
                    if not dest or not dest.is_active:
                        await asyncio.sleep(5)
                        continue

                    # 割当回線分の発信スロットを計算
                    allocated = dest.allocated_lines
                    current = sum(
                        1 for ac in self._active_calls.values() if ac.dest_id == dest_id
                    )
                    slots = allocated - current
                    if slots <= 0:
                        await asyncio.sleep(1)
                        continue

                    # 使用可能な回線を取得（Twilio優先）
                    lines_result = await db.execute(
                        select(CarrierLine)
                        .where(CarrierLine.destination_id == dest_id)
                        .where(CarrierLine.is_active == True)
                        .order_by(CarrierLine.priority)
                        .limit(slots)
                    )
                    lines = lines_result.scalars().all()

                    for line in lines:
                        asyncio.create_task(
                            self._make_single_call(dest, line)
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"dest_loop error (dest={dest_id}): {e}", exc_info=True)

            await asyncio.sleep(settings.redial_interval_sec)

    async def _make_single_call(self, dest: Destination, line: CarrierLine):
        """1件の発信を行い、結果に応じて転送またはリダイアル"""
        call_sid: Optional[str] = None

        async with AsyncSessionLocal() as db:
            log = CallLog(
                destination_id=dest.id,
                carrier_line_id=line.id,
                carrier=line.carrier,
                from_number=line.from_number,
                to_number=dest.phone_number,
                called_at=datetime.now(timezone.utc),
                result="calling",
            )
            
            is_conference = dest.transfer_on_ringing
            conference_name = None
            if is_conference:
                # コンファレンス名は一意にする
                import uuid
                conference_name = f"conf_{uuid.uuid4().hex[:12]}"
                log.conference_name = conference_name
                
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id

        try:
            # キャリア別発信
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
                # _active_calls を先に登録（ringing コールバックの競合を防ぐ）
                self._active_calls[call_sid] = ActiveCall(
                    call_sid, line.carrier, dest.id, log_id, is_conference, conference_name,
                    from_number=line.from_number,
                )
                # call_sid を DB に保存（ウェブフック更新に必要）
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

    async def on_call_ringing(self, call_sid: str):
        """
        宛先呼び出し音(ringing)検知時に呼ばれる（コンファレンスモード専用）。
        ここで空き担当者スマホをコンファレンスへ招待する。
        """
        ac = self._active_calls.get(call_sid)
        if not ac or not ac.is_conference:
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Operator)
                .where(Operator.is_active == True)
                .where(Operator.is_available == True)
                .order_by(Operator.priority)
                .limit(1)
            )
            operator = result.scalar_one_or_none()

            if not operator:
                logger.warning(f"No available operator for ringing call {call_sid}. Won't invite.")
                return

            operator.is_available = False

            log = await db.get(CallLog, ac.log_id)
            if log:
                # ログに担当者の情報を記録
                log.operator_id = operator.id
                log.transfer_to = operator.phone_number
            await db.commit()

        loop = asyncio.get_event_loop()
        try:
            from app.services.carrier import twilio_client
            from_number = ac.from_number  # ActiveCall に保持済みのため DB 検索不要
            if not from_number:
                logger.error("Could not find from_number for operator conference call")
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
                "note": "ringing_mode"
            })

        except Exception as e:
            logger.error(f"Conference operator call failed: {e}")
            # 失敗した場合は担当者を戻す
            async with AsyncSessionLocal() as db:
                operator = await db.get(Operator, operator.id)
                if operator:
                    operator.is_available = True
                    await db.commit()

    async def on_call_answered(self, call_sid: str):
        """
        通話確立時に呼ばれる。空き担当者スマホへ転送する。
        Webhookから直接呼ばれる想定。
        """
        ac = self._active_calls.get(call_sid)
        if not ac:
            return
            
        if ac.is_conference:
            # コンファレンスモードの場合は、すでにつながっているので何もしない（ログ時刻記録のみ）
            async with AsyncSessionLocal() as db:
                log = await db.get(CallLog, ac.log_id)
                if log:
                    log.result = "connected"
                    log.connected_at = datetime.now(timezone.utc)
                    await db.commit()
            return

        # 以下、通常の応答後転送モードの処理
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Operator)
                .where(Operator.is_active == True)
                .where(Operator.is_available == True)
                .order_by(Operator.priority)
                .limit(1)
            )
            operator = result.scalar_one_or_none()

            if not operator:
                logger.warning(f"No available operator for {call_sid}. Queuing...")
                # 全員話中 → 通話を保留して wait
                return

            # 担当者を busy 状態に
            operator.is_available = False
            await db.commit()

            # ログ更新
            log = await db.get(CallLog, ac.log_id)
            if log:
                log.result = "connected"
                log.connected_at = datetime.now(timezone.utc)
                log.operator_id = operator.id
                log.transfer_to = operator.phone_number
                await db.commit()

        # 転送実行
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

        # operator_calls からも削除
        for op_id, sid in list(self._operator_calls.items()):
            if sid == call_sid:
                self._operator_calls.pop(op_id, None)
                # 担当者を空き状態に戻す
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
