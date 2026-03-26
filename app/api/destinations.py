from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.destination import Destination
from app.schemas import DestinationCreate, DestinationUpdate, DestinationOut
from typing import List

router = APIRouter(prefix="/api/destinations", tags=["destinations"])


@router.get("", response_model=List[DestinationOut])
async def list_destinations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Destination).order_by(Destination.id))
    return result.scalars().all()


@router.post("", response_model=DestinationOut, status_code=201)
async def create_destination(data: DestinationCreate, db: AsyncSession = Depends(get_db)):
    dest = Destination(**data.model_dump())
    db.add(dest)
    await db.commit()
    await db.refresh(dest)
    return dest


@router.put("/{dest_id}", response_model=DestinationOut)
async def update_destination(dest_id: int, data: DestinationUpdate, db: AsyncSession = Depends(get_db)):
    dest = await db.get(Destination, dest_id)
    if not dest:
        raise HTTPException(status_code=404, detail="Not found")
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(dest, key, value)
    await db.commit()
    await db.refresh(dest)
    return dest


@router.delete("/{dest_id}", status_code=204)
async def delete_destination(dest_id: int, db: AsyncSession = Depends(get_db)):
    dest = await db.get(Destination, dest_id)
    if not dest:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(dest)
    await db.commit()
