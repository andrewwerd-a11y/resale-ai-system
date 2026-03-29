from __future__ import annotations
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item

router = APIRouter()


class ItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    brand: Optional[str] = None
    item_type: Optional[str] = None
    department: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    style: Optional[str] = None
    condition: Optional[str] = None
    condition_notes: Optional[str] = None
    author: Optional[str] = None
    book_format: Optional[str] = None
    franchise: Optional[str] = None
    character: Optional[str] = None
    estimated_price: Optional[float] = None
    list_price: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[str] = None


@router.get("")
def list_items(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    with get_session() as session:
        repo = ItemRepository(session)
        if q:
            items = repo.search(q)
        elif status:
            items = repo.list_by_status(status)
        else:
            items = repo.list_all()

    if category:
        items = [i for i in items if (i.category or "").lower() == category.lower()]

    return [_item_dict(i) for i in items]


@router.get("/counts")
def item_counts():
    with get_session() as session:
        repo = ItemRepository(session)
        return repo.count_by_status()


@router.get("/{sku}")
def get_item(sku: str):
    with get_session() as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku.upper())
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    return _item_dict(item)


@router.patch("/{sku}")
def update_item(sku: str, body: ItemUpdateRequest):
    with get_session() as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku.upper())
        if item is None:
            raise HTTPException(status_code=404, detail=f"Item {sku} not found")

        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if updates:
            updates["manual_override"] = True
        updated = item.model_copy(update=updates)
        saved = repo.upsert(updated)

    return _item_dict(saved)


@router.delete("/{sku}")
def delete_item(sku: str):
    """Mark item as archived (soft delete)."""
    with get_session() as session:
        repo = ItemRepository(session)
        ok = repo.update_status(sku.upper(), "archived")
    if not ok:
        raise HTTPException(status_code=404, detail=f"Item {sku} not found")
    return {"status": "archived", "sku": sku}


@router.get("/{sku}/image")
def serve_image(sku: str, path: str = Query(...)):
    """Serve a local image file by path. Falls back from .jpg to .jpeg if needed."""
    _MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png", ".webp": "image/webp",
             ".gif": "image/gif", ".bmp": "image/bmp"}

    p = Path(path)

    # .jpg ↔ .jpeg fallback: DB may store .jpg but file is .jpeg, or vice versa
    if not p.exists():
        alt = p.with_suffix(".jpeg") if p.suffix.lower() == ".jpg" else p.with_suffix(".jpg")
        if alt.exists():
            p = alt
        else:
            raise HTTPException(status_code=404, detail=f"Image not found: {path}")

    media_type = _MIME.get(p.suffix.lower(), "image/jpeg")
    return FileResponse(str(p), media_type=media_type)


def _item_dict(item: Item) -> dict:
    d = item.model_dump()
    # Ensure dates are ISO strings
    for key in ("date_created", "date_updated", "date_listed", "date_sold"):
        val = d.get(key)
        if val and hasattr(val, "isoformat"):
            d[key] = val.isoformat()
    return d
