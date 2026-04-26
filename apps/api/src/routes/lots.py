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
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    is_route_guard_enabled,
)

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
    try:
        body.skus = assert_route_skus_allowed(body.skus, "lots.create_lot", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    from packages.triage.src.lot_builder import LotBuilder
    builder = LotBuilder()
    result = builder.create_lot(body.skus, body.title, body.price, session)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"lot_sku": result.value}


@router.post("/dissolve/{lot_sku}")
def dissolve_lot(lot_sku: str, session: Session = Depends(get_session)):
    try:
        if is_route_guard_enabled():
            member_stmt = select(ItemRecord.sku).where(
                ItemRecord.lot_group_id == lot_sku,
                ItemRecord.sku != lot_sku,
            )
            member_rows = session.exec(member_stmt).all()
            member_skus = [row for row in member_rows if row]
            if member_skus:
                assert_route_skus_allowed(member_skus, "lots.dissolve_lot", require_non_empty=True)
            else:
                assert_route_sku_allowed(lot_sku, "lots.dissolve_lot")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    from packages.triage.src.lot_builder import LotBuilder
    builder = LotBuilder()
    result = builder.dissolve_lot(lot_sku, session)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.error)
    return {"lot_sku": lot_sku, "freed_members": result.value}
