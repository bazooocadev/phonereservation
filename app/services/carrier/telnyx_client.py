"""Telnyx API クライアント"""
import telnyx
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


def _init():
    telnyx.api_key = settings.telnyx_api_key


def make_call(from_number: str, to_number: str, webhook_base_url: str) -> str:
    """
    Telnyxで発信し、コールコントロールIDを返す
    """
    _init()
    call = telnyx.Call.create(
        connection_id=settings.telnyx_connection_id,
        from_=from_number,
        to=to_number,
        webhook_url=f"{webhook_base_url}/api/webhooks/telnyx",
        webhook_url_method="POST",
        timeout_secs=30,
        answering_machine_detection="detect",
    )
    call_id = call.call_leg_id or call.call_control_id
    logger.info(f"Telnyx call created: ID={call_id} from={from_number} to={to_number}")
    return call_id


def transfer_call(call_control_id: str, to_number: str):
    """既存通話を担当者スマホへ転送"""
    _init()
    try:
        call = telnyx.Call.retrieve(call_control_id)
        call.transfer(to=to_number, timeout_secs=30)
        logger.info(f"Telnyx transfer: {call_control_id} -> {to_number}")
    except Exception as e:
        logger.error(f"Telnyx transfer error: {e}")


def hangup_call(call_control_id: str):
    """通話を強制切断"""
    _init()
    try:
        call = telnyx.Call.retrieve(call_control_id)
        call.hangup()
        logger.info(f"Telnyx hangup: {call_control_id}")
    except Exception as e:
        logger.warning(f"Telnyx hangup failed: {e}")
