from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlmodel import Session

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.csv_writer import EbayCSVWriter
from packages.spreadsheet.src.master_sheet import MasterSheetWriter

router = APIRouter()


@router.post("/ebay-csv")
def generate_ebay_csv(session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    items = repo.list_by_status("export_ready")
    if not items:
        return {"message": "No export_ready items found.", "count": 0}
    writer = EbayCSVWriter()
    path = writer.write(items)
    return {"message": f"CSV written with {len(items)} items.", "path": str(path), "count": len(items)}


@router.post("/master-sheet")
def generate_master_sheet(session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    items = repo.get_all()
    writer = MasterSheetWriter()
    path = writer.write(items)
    return {"message": f"Master sheet written with {len(items)} items.", "path": str(path)}


@router.get("/stats")
def export_stats(session: Session = Depends(get_session)):
    repo = ItemRepository(session)
    ready = repo.list_by_status("export_ready")
    return {"export_ready": len(ready)}
