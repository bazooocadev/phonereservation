"""Twilio API クライアント"""
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial, Conference
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


def get_twilio_client() -> Client:
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


# ─── 通常発信（応答後転送モード）────────────────────────────

def make_call(from_number: str, to_number: str, webhook_base_url: str) -> str:
    """
    Twilio で発信し、コールSIDを返す。
    応答後にGatherで音声収集 → 混雑検知 → 担当者へ転送。
    """
    client = get_twilio_client()
    call = client.calls.create(
        from_=from_number,
        to=to_number,
        url=f"{webhook_base_url}/api/webhooks/twilio/twiml",
        status_callback=f"{webhook_base_url}/api/webhooks/twilio",
        status_callback_method="POST",
        status_callback_event=["completed", "failed", "busy", "no-answer"],
        timeout=30,
    )
    logger.info(f"Twilio call created: SID={call.sid} from={from_number} to={to_number}")
    return call.sid


# ─── コンファレンス発信（呼び出し音転送モード）──────────────

def make_call_conference(
    from_number: str,
    to_number: str,
    conference_name: str,
    webhook_base_url: str,
) -> str:
    """
    宛先へ発信し、コンファレンスルームへ入れる。
    status_callback に 'ringing' を含め、鳴り始めで on_call_ringing() を起動する。
    """
    client = get_twilio_client()
    call = client.calls.create(
        from_=from_number,
        to=to_number,
        url=f"{webhook_base_url}/api/webhooks/twilio/twiml/conference"
            f"?room={conference_name}&wait_for_operator=true",
        status_callback=f"{webhook_base_url}/api/webhooks/twilio",
        status_callback_method="POST",
        status_callback_event=["ringing", "answered", "completed", "failed", "busy", "no-answer"],
        timeout=30,
    )
    logger.info(f"Twilio conference call: SID={call.sid} room={conference_name} to={to_number}")
    return call.sid


def call_operator_to_conference(
    from_number: str,
    operator_number: str,
    conference_name: str,
    webhook_base_url: str,
) -> str:
    """
    担当者スマホをコンファレンスへ招待（宛先 ringing 中に発信）。
    スマホが応答すると「呼び出し音待機音楽」が流れ、
    宛先が応答した瞬間に自動でつながる。
    """
    client = get_twilio_client()
    call = client.calls.create(
        from_=from_number,
        to=operator_number,
        url=f"{webhook_base_url}/api/webhooks/twilio/twiml/conference"
            f"?room={conference_name}&wait_for_operator=false",
        status_callback=f"{webhook_base_url}/api/webhooks/twilio/operator-callback",
        status_callback_method="POST",
        status_callback_event=["answered", "completed", "failed", "no-answer"],
        timeout=30,
    )
    logger.info(f"Twilio operator call: SID={call.sid} room={conference_name} to={operator_number}")
    return call.sid


# ─── 通話操作 ──────────────────────────────────────────────

def transfer_call(call_sid: str, to_number: str, webhook_base_url: str):
    """
    確立済み通話を担当者スマホへ転送（応答後転送モード用）
    """
    client = get_twilio_client()
    client.calls(call_sid).update(
        url=f"{webhook_base_url}/api/webhooks/twilio/transfer-twiml?to={to_number}",
        method="POST",
    )
    logger.info(f"Twilio transfer: SID={call_sid} -> {to_number}")


def hangup_call(call_sid: str):
    """通話を強制切断"""
    client = get_twilio_client()
    try:
        client.calls(call_sid).update(status="completed")
        logger.info(f"Twilio hangup: SID={call_sid}")
    except Exception as e:
        logger.warning(f"Twilio hangup failed: {e}")


# ─── TwiML ビルダー ────────────────────────────────────────

def build_gather_twiml(webhook_base_url: str) -> str:
    """応答音声を収集するTwiMLを生成（混雑アナウンス検知用）"""
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        language="ja-JP",
        timeout=5,
        action=f"{webhook_base_url}/api/webhooks/twilio",
        method="POST",
    )
    response.append(gather)
    response.pause(length=2)
    return str(response)


def build_conference_twiml(conference_name: str, wait_for_operator: bool) -> str:
    """
    コンファレンス参加用TwiMLを生成。
    wait_for_operator=True  → 宛先側: スマホが来るまで待機音
    wait_for_operator=False → スマホ側: すぐコンファレンスへ入る
    """
    response = VoiceResponse()
    dial = Dial()
    conf_kwargs = dict(
        start_conference_on_enter=not wait_for_operator,
        end_conference_on_exit=True,
        muted=False,
    )
    # 担当者側はwaitUrl不要（入室と同時にコンファレンス開始）
    # 空文字を渡すとTwilioがURLフェッチ失敗するため、宛先側のみ設定する
    if wait_for_operator:
        conf_kwargs["wait_url"] = "https://com.twilio.music.classical.s3.amazonaws.com/ClockworkWaltz.mp3"
        conf_kwargs["wait_method"] = "GET"
    conf = Conference(conference_name, **conf_kwargs)
    dial.append(conf)
    response.append(dial)
    return str(response)


def build_transfer_twiml(to_number: str, webhook_base_url: str) -> str:
    """転送先スマホへダイヤルするTwiMLを生成（応答後転送モード用）"""
    response = VoiceResponse()
    dial = Dial(
        action=f"{webhook_base_url}/api/webhooks/twilio/transfer",
        method="POST",
        timeout=30,
        record="record-from-start",
    )
    dial.number(to_number)
    response.append(dial)
    return str(response)


