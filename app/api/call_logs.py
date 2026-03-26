from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.call_log import CallLog
from app.schemas import CallLogOut
from typing import List, Optional
from datetime import datetime, date

router = APIRouter(prefix="/api/call-logs", tags=["call-logs"])


@router.get("", response_model=List[CallLogOut])
async def list_call_logs(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, le=500),
    offset: int = 0,
    result: Optional[str] = None,
    destination_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    q = (
        select(CallLog)
        .options(
            selectinload(CallLog.destination),
            selectinload(CallLog.carrier_line),
            selectinload(CallLog.operator),
        )
        .order_by(CallLog.called_at.desc())
    )
    if result:
        q = q.where(CallLog.result == result)
    if destination_id:
        q = q.where(CallLog.destination_id == destination_id)
    if date_from:
        q = q.where(CallLog.called_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(CallLog.called_at <= datetime.combine(date_to, datetime.max.time()))
    q = q.limit(limit).offset(offset)
    rows = await db.execute(q)
    return rows.scalars().all()
