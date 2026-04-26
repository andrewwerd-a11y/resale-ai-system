"""
Sourcing cost tracking endpoints.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from packages.data.src.db.sqlite import get_session
from packages.data.src.models.sourcing_batch import SourcingBatch
from packages.data.src.repositories.item_repo import ItemRepository
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    is_route_guard_enabled,
)

router = APIRouter()


class BatchCreate(BaseModel):
    label: str
    total_cost: float
    item_count: int
    sourcing_date: str
    location: str | None = None
    notes: str | None = None


class AssignBatchBody(BaseModel):
    skus: list[str]


class SetCostBody(BaseModel):
    cost: float
    sourcing_location: str | None = None
    sourcing_batch: str | None = None


@router.post("/batch")
def create_batch(body: BatchCreate, session: Session = Depends(get_session)):
    if is_route_guard_enabled():
        try:
            # This route has no SKU filter; block in guarded E2E mode.
            assert_route_skus_allowed([], "sourcing.create_batch", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    item_count = max(body.item_count, 1)
    cost_per_item = round(body.total_cost / item_count, 2)
    try:
        sd = datetime.fromisoformat(body.sourcing_date)
    except (ValueError, TypeError):
        sd = datetime.utcnow()

    batch = SourcingBatch(
        label=body.label,
        total_cost=body.total_cost,
        item_count=item_count,
        cost_per_item=cost_per_item,
        sourcing_date=sd,
        location=body.location,
        notes=body.notes,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch.model_dump()


@router.get("/batches")
def list_batches(session: Session = Depends(get_session)):
    batches = session.exec(select(SourcingBatch)).all()
    return sorted([b.model_dump() for b in batches], key=lambda x: str(x.get("created_at", "")), reverse=True)


@router.post("/assign/{batch_id}")
def assign_batch(
    batch_id: str,
    body: AssignBatchBody,
    session: Session = Depends(get_session),
):
    try:
        body.skus = assert_route_skus_allowed(body.skus, "sourcing.assign_batch", require_non_empty=True)
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    batch = session.get(SourcingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    repo = ItemRepository(session)
    updated = []
    for sku in body.skus:
        item = repo.get_by_sku(sku)
        if item:
            item.cost = batch.cost_per_item
            item.cost_manual = True
            item.sourcing_batch = batch_id
            if batch.location:
                item.sourcing_location = batch.location
            repo.upsert(item)
            updated.append(sku)

    return {
        "batch_id": batch_id,
        "cost_per_item": batch.cost_per_item,
        "assigned": len(updated),
        "skus": updated,
    }


@router.patch("/item/{sku}")
def set_item_cost(
    sku: str,
    body: SetCostBody,
    session: Session = Depends(get_session),
):
    try:
        assert_route_sku_allowed(sku, "sourcing.set_item_cost")
    except E2ESafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    repo = ItemRepository(session)
    item = repo.get_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")

    item.cost = body.cost
    item.cost_manual = True
    if body.sourcing_location is not None:
        item.sourcing_location = body.sourcing_location
    if body.sourcing_batch is not None:
        item.sourcing_batch = body.sourcing_batch
    saved = repo.upsert(item)
    return {"sku": sku, "cost": saved.cost, "cost_manual": saved.cost_manual}
