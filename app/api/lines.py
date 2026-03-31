from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.database import get_db
from app.models.carrier_line import CarrierLine
from app.models.call_log import CallLog
from app.schemas import CarrierLineCreate, CarrierLineUpdate, CarrierLineOut
from typing import List

router = APIRouter(prefix="/api/lines", tags=["lines"])


@router.get("", response_model=List[CarrierLineOut])
async def list_lines(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CarrierLine).order_by(CarrierLine.destination_id, CarrierLine.priority)
    )
    return result.scalars().all()


@router.post("", response_model=CarrierLineOut, status_code=201)
async def create_line(data: CarrierLineCreate, db: AsyncSession = Depends(get_db)):
    line = CarrierLine(**data.model_dump())
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


@router.put("/{line_id}", response_model=CarrierLineOut)
async def update_line(line_id: int, data: CarrierLineUpdate, db: AsyncSession = Depends(get_db)):
    line = await db.get(CarrierLine, line_id)
    if not line:
        raise HTTPException(status_code=404, detail="Not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(line, key, value)
    await db.commit()
    await db.refresh(line)
    return line


@router.delete("/{line_id}", status_code=204)
async def delete_line(line_id: int, db: AsyncSession = Depends(get_db)):
    line = await db.get(CarrierLine, line_id)
    if not line:
        raise HTTPException(status_code=404, detail="Not found")
    # 外部キー参照をNULLに解除してから削除
    await db.execute(update(CallLog).where(CallLog.carrier_line_id == line_id).values(carrier_line_id=None))
    await db.delete(line)
    await db.commit()
