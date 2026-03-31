from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.database import get_db
from app.models.operator import Operator
from app.models.call_log import CallLog
from app.schemas import OperatorCreate, OperatorUpdate, OperatorOut
from typing import List

router = APIRouter(prefix="/api/operators", tags=["operators"])


@router.get("", response_model=List[OperatorOut])
async def list_operators(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Operator).order_by(Operator.priority))
    return result.scalars().all()


@router.post("", response_model=OperatorOut, status_code=201)
async def create_operator(data: OperatorCreate, db: AsyncSession = Depends(get_db)):
    op = Operator(**data.model_dump())
    db.add(op)
    await db.commit()
    await db.refresh(op)
    return op


@router.put("/{op_id}", response_model=OperatorOut)
async def update_operator(op_id: int, data: OperatorUpdate, db: AsyncSession = Depends(get_db)):
    op = await db.get(Operator, op_id)
    if not op:
        raise HTTPException(status_code=404, detail="Not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(op, key, value)
    await db.commit()
    await db.refresh(op)
    return op


@router.delete("/{op_id}", status_code=204)
async def delete_operator(op_id: int, db: AsyncSession = Depends(get_db)):
    op = await db.get(Operator, op_id)
    if not op:
        raise HTTPException(status_code=404, detail="Not found")
    # 外部キー参照をNULLに解除してから削除
    await db.execute(update(CallLog).where(CallLog.operator_id == op_id).values(operator_id=None))
    await db.delete(op)
    await db.commit()


@router.post("/{op_id}/force-hangup", status_code=200)
async def force_hangup(op_id: int, db: AsyncSession = Depends(get_db)):
    """担当者への転送通話を強制切断"""
    from app.services.redial_engine import engine as redial_engine
    op = await db.get(Operator, op_id)
    if not op:
        raise HTTPException(status_code=404, detail="Not found")
    await redial_engine.hangup_operator(op_id)
    op.is_available = True
    await db.commit()
    return {"message": "Hangup requested"}
