from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.call_log import CallLog
from app.schemas import CallLogOut
from typing import List, Optional
from datetime import datetime, date, timezone

router = APIRouter(prefix="/api/call-logs", tags=["call-logs"])


@router.get("", response_model=List[CallLogOut])
async def list_call_logs(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, le=500),
    offset: int = 0,
    result: Optional[str] = None,
    active_only: bool = False,
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
    if active_only:
        q = q.where(CallLog.ended_at == None)
    if destination_id:
        q = q.where(CallLog.destination_id == destination_id)
    if date_from:
        q = q.where(CallLog.called_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(CallLog.called_at <= datetime.combine(date_to, datetime.max.time()))
    q = q.limit(limit).offset(offset)
    rows = await db.execute(q)
    return rows.scalars().all()


@router.post("/{log_id}/hangup", status_code=200)
async def hangup_call_log(log_id: int, db: AsyncSession = Depends(get_db)):
    """接続中の通話を強制切断する（サーバー→接続先）"""
    from app.services.redial_engine import engine as redial_engine

    log = await db.get(CallLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Not found")
    if not log.call_sid or not log.carrier:
        raise HTTPException(status_code=400, detail="call_sid が見つかりません")
    if log.result not in ("calling", "connected"):
        raise HTTPException(status_code=400, detail="この通話はすでに終了しています")

    # エンジン内のアクティブ通話として管理されていれば正式なクリーンアップ
    if log.call_sid in redial_engine._active_calls:
        await redial_engine.on_call_ended(log.call_sid, "cancelled")

    # Twilio/Telnyx へハングアップリクエスト
    await redial_engine._do_hangup(log.call_sid, log.carrier)

    # DB を直接更新（on_call_ended で処理済みの場合は上書きにならない）
    fresh = await db.get(CallLog, log_id)
    if fresh and fresh.result in ("calling", "connected"):
        fresh.result = "cancelled"
        fresh.ended_at = datetime.now(timezone.utc)
        await db.commit()

    return {"message": "切断リクエストを送信しました"}
