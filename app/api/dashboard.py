from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.call_log import CallLog
from app.models.destination import Destination
from app.models.operator import Operator
from app.schemas import DashboardStats
from datetime import datetime, timezone
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    from app.services.redial_engine import engine as redial_engine

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 今日の通話集計
    total_q = await db.execute(
        select(func.count()).where(CallLog.called_at >= today_start)
    )
    total_calls_today = total_q.scalar() or 0

    connected_q = await db.execute(
        select(func.count()).where(
            CallLog.called_at >= today_start,
            CallLog.result == "connected"
        )
    )
    connected_calls_today = connected_q.scalar() or 0

    duration_q = await db.execute(
        select(func.sum(CallLog.duration_sec)).where(
            CallLog.called_at >= today_start,
            CallLog.result == "connected"
        )
    )
    total_duration_today = duration_q.scalar() or 0

    cost_q = await db.execute(
        select(func.sum(CallLog.cost)).where(CallLog.called_at >= today_start)
    )
    estimated_cost_today = round(cost_q.scalar() or 0.0, 4)

    # 宛先一覧
    dest_rows = await db.execute(select(Destination).order_by(Destination.id))
    destinations = [
        {
            "id": d.id,
            "name": d.name,
            "phone_number": d.phone_number,
            "is_active": d.is_active,
            "allocated_lines": d.allocated_lines,
        }
        for d in dest_rows.scalars().all()
    ]

    # 担当者一覧
    op_rows = await db.execute(select(Operator).order_by(Operator.priority))
    operators = [
        {
            "id": o.id,
            "name": o.name,
            "is_available": o.is_available,
            "is_active": o.is_active,
            "priority": o.priority,
        }
        for o in op_rows.scalars().all()
    ]

    return DashboardStats(
        total_calls_today=total_calls_today,
        connected_calls_today=connected_calls_today,
        total_duration_today=total_duration_today,
        estimated_cost_today=estimated_cost_today,
        active_calls=redial_engine.active_call_count,
        engine_running=redial_engine.is_running,
        destinations=destinations,
        operators=operators,
    )


@router.get("/carrier-capacity")
async def get_carrier_capacity(db: AsyncSession = Depends(get_db)):
    """Twilio/TelnyxアカウントのAPIから回線使用状況を取得"""
    from app.services.carrier.twilio_client import get_twilio_client
    from app.config import get_settings
    from app.models.call_log import CallLog
    import httpx
    settings = get_settings()

    result = {
        "twilio": {"owned": 0, "active": 0, "available": 0, "error": None},
        "telnyx": {"owned": 0, "active": 0, "available": 0, "error": None},
    }

    # ── Twilio ──────────────────────────────────────────────
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        result["twilio"]["error"] = "未設定"
    else:
        def fetch_twilio():
            try:
                client = get_twilio_client()
                numbers = client.incoming_phone_numbers.list()
                active_calls = client.calls.list(status="in-progress")
                active_from = {c.from_ for c in active_calls}
                phone_list = [
                    {"number": n.phone_number, "friendly_name": n.friendly_name, "in_use": n.phone_number in active_from}
                    for n in numbers
                ]
                return phone_list, len(active_calls), None
            except Exception as e:
                return [], 0, str(e)

        phone_list, active, err = await asyncio.to_thread(fetch_twilio)
        result["twilio"] = {
            "owned": len(phone_list),
            "active": active,
            "available": max(0, len(phone_list) - active),
            "numbers": phone_list,
            "error": err,
        }

    # ── Telnyx ──────────────────────────────────────────────
    if not settings.telnyx_api_key:
        result["telnyx"]["error"] = "未設定"
    else:
        async def fetch_telnyx_numbers():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://api.telnyx.com/v2/phone_numbers",
                        headers={"Authorization": f"Bearer {settings.telnyx_api_key}"},
                        params={"page[size]": 100},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return [
                        {"number": n["phone_number"], "friendly_name": n.get("friendly_name") or ""}
                        for n in data.get("data", [])
                    ], None
            except Exception as e:
                return [], str(e)

        numbers, err = await fetch_telnyx_numbers()

        # 使用中回線数はDBのアクティブ通話（carrier=telnyx）から取得
        active_rows = await db.execute(
            select(CallLog.from_number).where(
                CallLog.carrier == "telnyx",
                CallLog.result == "calling",
            )
        )
        active_numbers = {r[0] for r in active_rows.all() if r[0]}
        active = len(active_numbers)

        phone_list = [
            {**n, "in_use": n["number"] in active_numbers}
            for n in numbers
        ]
        result["telnyx"] = {
            "owned": len(phone_list),
            "active": active,
            "available": max(0, len(phone_list) - active),
            "numbers": phone_list,
            "error": err,
        }

    return result


@router.post("/start")
async def start_redial():
    from app.services.redial_engine import engine as redial_engine
    await redial_engine.start()
    return {"status": "started"}


@router.post("/stop")
async def stop_redial():
    from app.services.redial_engine import engine as redial_engine
    await redial_engine.stop()
    return {"status": "stopped"}
