"""
Lot management API endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from packages.data.src.db.sqlite import get_session

router = APIRouter()


class LotCreate(BaseModel):
    skus: list[str]
    title: str
    price: float = 0.0


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
