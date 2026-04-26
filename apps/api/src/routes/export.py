from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.csv_writer import EbayCSVWriter
from packages.spreadsheet.src.master_sheet import MasterSheetWriter
from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_route_skus_allowed,
    is_route_guard_enabled,
    parse_sku_list,
)

router = APIRouter()


@router.post("/ebay-csv")
def generate_ebay_csv(
    skus: str = "",
    e2e_only: bool = False,
    session: Session = Depends(get_session),
):
    repo = ItemRepository(session)
    selected = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            selected = assert_route_skus_allowed(selected, "export.ebay_csv", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not selected:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    items = repo.list_by_status("export_ready")
    if selected:
        allowed = set(selected)
        items = [i for i in items if (i.sku or "").upper() in allowed]
    if not items:
        return {"message": "No export_ready items found.", "count": 0}
    writer = EbayCSVWriter()
    path = writer.write(items)
    return {"message": f"CSV written with {len(items)} items.", "path": str(path), "count": len(items)}


@router.post("/master-sheet")
def generate_master_sheet(
    skus: str = "",
    e2e_only: bool = False,
    session: Session = Depends(get_session),
):
    repo = ItemRepository(session)
    selected = parse_sku_list(skus)
    if is_route_guard_enabled():
        try:
            selected = assert_route_skus_allowed(selected, "export.master_sheet", require_non_empty=True)
        except E2ESafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if e2e_only and not selected:
        raise HTTPException(status_code=400, detail="e2e_only requires explicit skus")

    items = repo.get_all()
    if selected:
        allowed = set(selected)
        items = [i for i in items if (i.sku or "").upper() in allowed]
    writer = MasterSheetWriter()
    path = writer.write(items)
    return {"message": f"Master sheet written with {len(items)} items.", "path": str(path)}


@router.get("/stats")
def export_stats(session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    ready = repo.list_by_status("export_ready")
    return {"export_ready": len(ready)}
