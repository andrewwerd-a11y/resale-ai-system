from __future__ import annotations
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from packages.data.src.db.sqlite import get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.csv_writer import generate_ebay_csv
from packages.spreadsheet.src.master_sheet import generate_csv, generate_excel

router = APIRouter()


@router.get("/ebay-csv")
def export_ebay_csv():
    """Generate and download eBay bulk CSV."""
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_by_statuses(["approved", "export_ready", "exported"])

    if not items:
        return {"message": "No items ready for export", "count": 0}

    output = generate_ebay_csv(items)
    return FileResponse(
        str(output),
        media_type="text/csv",
        filename=output.name,
    )


@router.get("/master-csv")
def export_master_csv():
    """Generate and download full master inventory CSV."""
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_all()

    output = generate_csv(items)
    return FileResponse(
        str(output),
        media_type="text/csv",
        filename=output.name,
    )


@router.get("/master-excel")
def export_master_excel():
    """Generate and download full master inventory Excel file."""
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_all()

    output = generate_excel(items)
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(
        str(output),
        media_type=media,
        filename=output.name,
    )


@router.get("/ready-count")
def export_ready_count():
    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_by_statuses(["approved", "export_ready"])
    return {"count": len(items)}
