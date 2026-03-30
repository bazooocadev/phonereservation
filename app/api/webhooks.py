from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.call_log import CallLog
from app.models.operator import Operator
from app.services import websocket_manager
from app.config import get_settings
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
settings = get_settings()

# NTT混雑アナウンス判断キーワード（一部）
CONGESTION_KEYWORDS = [
    "混雑", "ただいま電話が大変込み合っております", "こみあって",
    "お繋ぎできません", "おつなぎ", "お待ちください"
]


def _is_congested(speech_result: str) -> bool:
    """音声認識テキストにNTT混雑アナウンスのキーワードが含まれるか判定"""
    for kw in CONGESTION_KEYWORDS:
        if kw in speech_result:
            return True
    return False


@router.api_route("/twilio/twiml", methods=["GET", "POST"])
async def twilio_twiml(request: Request):
    """Twilioが発信後・応答時に取得するTwiML（Gather音声収集）"""
    from app.services.carrier.twilio_client import build_gather_twiml
    xml = build_gather_twiml(settings.webhook_base_url)
    return Response(content=xml, media_type="text/xml")


@router.api_route("/twilio/twiml/conference", methods=["GET", "POST"])
async def twilio_twiml_conference(request: Request):
    """コンファレンスモード用TwiML（宛先側・担当者側共用）"""
    from app.services.carrier.twilio_client import build_conference_twiml
    params = dict(request.query_params)
    conference_name = params.get("room", "default_room")
    wait_for_operator = params.get("wait_for_operator", "true").lower() == "true"
    xml = build_conference_twiml(conference_name, wait_for_operator)
    return Response(content=xml, media_type="text/xml")


@router.api_route("/twilio/transfer-twiml", methods=["GET", "POST"])
async def twilio_transfer_twiml(request: Request):
    """応答後転送モード用TwiML（担当者スマホへのDial）"""
    from app.services.carrier.twilio_client import build_transfer_twiml
    params = dict(request.query_params)
    to_number = params.get("to", "")
    if not to_number:
        return Response(content="<Response><Hangup/></Response>", media_type="text/xml")
    xml = build_transfer_twiml(to_number, settings.webhook_base_url)
    return Response(content=xml, media_type="text/xml")


@router.post("/twilio")
async def twilio_status_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Twilioのステータスコールバック / Gatherの音声収集結果も受け取る"""
    from app.services.redial_engine import engine as redial_engine

    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    duration = form.get("CallDuration", None)
    speech_result = form.get("SpeechResult", "")

    logger.info(f"Twilio callback: SID={call_sid} Status={call_status} Speech={speech_result!r}")

    is_congested = bool(speech_result and _is_congested(speech_result))

    if call_status == "ringing":
        # コンファレンスモード用：宛先が鳴り始めたタイミング
        await redial_engine.on_call_ringing(call_sid)
        result_label = "calling"

    elif is_congested:
        # 混雑アナウンス検知 → 通話を切って再発信キューへ
        logger.info(f"Congestion detected for {call_sid}")
        result_label = "congested"
        await redial_engine.on_call_ended(call_sid, "congested")

    elif speech_result and not is_congested:
        # 音声取得完了・混雑なし → 通話確立とみなして担当者へ転送
        result_label = "connected"
        await redial_engine.on_call_answered(call_sid)

    elif call_status in ("busy", "failed", "no-answer", "canceled"):
        # 発信失敗系 → 再発信へ
        result_label = _map_twilio_status(call_status)
        await redial_engine.on_call_ended(call_sid, result_label)

    elif call_status == "completed":
        # 通話完了（転送後のハングアップ等）
        # すでに congested/busy/failed 等で処理済みの場合は上書きしない
        result_label = "connected"
        duration_sec = int(duration) if duration else 0
        result = await db.execute(select(CallLog).where(CallLog.call_sid == call_sid))
        existing_log = result.scalar_one_or_none()
        terminal_results = {"congested", "busy", "failed", "no-answer"}
        if existing_log and existing_log.result in terminal_results:
            return Response(content="", media_type="text/xml")
        await redial_engine.on_call_ended(call_sid, result_label, duration_sec)

    else:
        result_label = call_status

    # DBログ更新
    result = await db.execute(select(CallLog).where(CallLog.call_sid == call_sid))
    log = result.scalar_one_or_none()
    if log:
        log.result = result_label
        if duration:
            dur_sec = int(duration)
            log.duration_sec = dur_sec
            log.cost = round(dur_sec / 60 * settings.twilio_cost_per_min, 6)
        if call_status in ("completed", "busy", "failed", "no-answer", "canceled") or is_congested:
            log.ended_at = datetime.now(timezone.utc)
        await db.commit()

    # WebSocketへブロードキャスト
    await websocket_manager.broadcast({
        "type": "call_update",
        "call_sid": call_sid,
        "status": call_status,
        "result": result_label,
    })

    return Response(content="", media_type="text/xml")


@router.post("/twilio/transfer")
async def twilio_transfer_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Twilioの転送完了コールバック"""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    dial_call_status = form.get("DialCallStatus", "")

    result = await db.execute(select(CallLog).where(CallLog.call_sid == call_sid))
    log = result.scalar_one_or_none()

    if log:
        success = dial_call_status == "completed"
        log.transfer_connected = 1 if success else 2
        if success:
            log.connected_at = datetime.now(timezone.utc)

        # 担当者を空き状態に戻す
        if log.operator_id:
            op = await db.get(Operator, log.operator_id)
            if op:
                op.is_available = True
        await db.commit()

    await websocket_manager.broadcast({
        "type": "transfer_update",
        "call_sid": call_sid,
        "dial_status": dial_call_status,
    })

    return Response(content="", media_type="text/xml")


@router.post("/twilio/operator-callback")
async def twilio_operator_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """コンファレンスモードでの担当者側（スマホ）のステータスコールバック"""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    
    logger.info(f"Operator callback: SID={call_sid} Status={call_status}")
    
    # 担当者側が応答失敗・ハングアップした場合、担当者を空きに戻す
    if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
        result = await db.execute(select(CallLog).where(CallLog.operator_call_sid == call_sid))
        log = result.scalar_one_or_none()
        if log and log.operator_id:
            op = await db.get(Operator, log.operator_id)
            if op:
                op.is_available = True
                await db.commit()
    
    return Response(content="", media_type="text/xml")


@router.post("/telnyx")
async def telnyx_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Telnyxのイベントウェブフック"""
    from app.services.redial_engine import engine as redial_engine

    body = await request.json()
    event_type = body.get("data", {}).get("event_type", "")
    payload = body.get("data", {}).get("payload", {})
    call_control_id = payload.get("call_control_id", "")
    call_sid = payload.get("call_leg_id", call_control_id)

    logger.info(f"Telnyx event: {event_type} CID={call_control_id}")

    result_label = _map_telnyx_event(event_type)

    if event_type == "call.answered":
        # 通話確立 → 担当者スマホへ転送
        await redial_engine.on_call_answered(call_sid)

    elif event_type == "call.hangup":
        secs = int(payload.get("call_duration_secs") or 0)
        await redial_engine.on_call_ended(call_sid, "connected", secs)

    elif event_type in ("call.rejected", "call.machine.detection.ended"):
        label = "busy" if event_type == "call.rejected" else "congested"
        await redial_engine.on_call_ended(call_sid, label)

    result = await db.execute(select(CallLog).where(CallLog.call_sid == call_sid))
    log = result.scalar_one_or_none()

    if log and result_label:
        log.result = result_label
        if event_type == "call.hangup":
            log.ended_at = datetime.now(timezone.utc)
            log.duration_sec = int(payload.get("call_duration_secs") or 0)
            if log.operator_id:
                op = await db.get(Operator, log.operator_id)
                if op:
                    op.is_available = True
        await db.commit()

    await websocket_manager.broadcast({
        "type": "telnyx_event",
        "event": event_type,
        "call_sid": call_sid,
        "result": result_label,
    })

    return {"status": "ok"}


def _map_twilio_status(status: str) -> str:
    mapping = {
        "completed": "connected",
        "busy": "busy",
        "no-answer": "no-answer",
        "failed": "failed",
        "canceled": "failed",
    }
    return mapping.get(status, status)


def _map_telnyx_event(event: str) -> str:
    mapping = {
        "call.answered": "connected",
        "call.hangup": "connected",
        "call.rejected": "busy",
        "call.machine.detection.ended": "congested",
    }
    return mapping.get(event, "")
