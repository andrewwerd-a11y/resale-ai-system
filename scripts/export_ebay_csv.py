"""
export_ebay_csv.py — export all approved/export_ready items to eBay bulk upload CSV.

Usage:
    uv run python scripts/export_ebay_csv.py
    uv run python scripts/export_ebay_csv.py --status approved
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from sqlmodel import Session

from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.csv_writer import EbayCSVWriter
from packages.spreadsheet.src.master_sheet import MasterSheetWriter

console = Console()


def run_export(status: str = "export_ready") -> None:
    init_db()
    console.rule("[bold]eBay CSV Export[/bold]")

    with Session(engine) as session:
        repo = ItemRepository(session)
        items = repo.list_by_status(status)

        if not items:
            console.print(f"[yellow]No items with status '{status}' found.[/yellow]")
            console.print("Approve items in the Review Queue first.")
            return

        console.print(f"Exporting [green]{len(items)}[/green] items...")

        # Write eBay CSV
        writer = EbayCSVWriter()
        csv_path = writer.write(items)
        console.print(f"\n[green]eBay CSV:[/green] {csv_path}")

        # Update status to 'exported'
        for item in items:
            repo.update_status(item.sku, ItemStatus.EXPORTED)

        # Refresh and write master sheet
        all_items = repo.get_all()
        sheet_writer = MasterSheetWriter()
        sheet_path = sheet_writer.write(all_items)
        console.print(f"[green]Master sheet:[/green] {sheet_path}")

    console.print()
    console.print("Upload the CSV to eBay Seller Hub → Reports → Upload a file")
    console.print("Select template type: [cyan]Active listings[/cyan]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", default="export_ready",
                        help="Item status to export (default: export_ready)")
    args = parser.parse_args()
    run_export(args.status)
