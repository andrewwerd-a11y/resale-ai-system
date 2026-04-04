"""
Lot management API endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from packages.data.src.db.sqlite import get_session
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import _from_record

router = APIRouter()


class LotCreate(BaseModel):
    skus: list[str]
    title: str
    price: float = 0.0


@router.get("")
def list_lots(session: Session = Depends(get_session)):
    """Return all items where item_mode == 'lot'."""
    stmt = select(ItemRecord).where(ItemRecord.item_mode == "lot")
    records = session.exec(stmt).all()
    return [_from_record(r).model_dump() for r in records]


@router.post("/create")
def create_lot(body: LotCreate, session: Session = Depends(get_session)):
    from packages.triage.src.lot_builder import LotBuilder
    builder = LotBuilder()
    result = builder.create_lot(body.skus, body.title, body.price, session)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"lot_sku": result.value}


@router.post("/dissolve/{lot_sku}")
def dissolve_lot(lot_sku: str, session: Session = Depends(get_session)):
    from packages.triage.src.lot_builder import LotBuilder
    builder = LotBuilder()
    result = builder.dissolve_lot(lot_sku, session)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.error)
    return {"lot_sku": lot_sku, "freed_members": result.value}
