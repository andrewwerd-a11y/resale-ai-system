"""
backfill.py — migrate your existing inventory into the system.

Usage (Windows PowerShell):
    uv run python scripts/backfill.py --source "C:\\path\\to\\Inventory_Photos"

What it does:
  1. Scans every SKU folder (BK-000001, CL-000001, etc.)
  2. Preserves all existing SKUs — nothing is renamed or overwritten
  3. Initialises the SKU registry so new items never collide
  4. Creates an item record in SQLite for each folder
  5. Sets status = pending_intake (ready for AI analysis)
  6. Logs every action — safe to re-run (idempotent)

After running this, use the intake worker or browser UI to trigger
AI analysis on the imported items.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Ensure repo root is on path when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from sqlmodel import Session

from packages.core.src.config import get_settings, get_sku_prefixes
from packages.core.src.constants import ItemStatus, ItemMode
from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.models.batch_record import BatchRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.data.src.repositories.sku_repo import SKURepository
from packages.domain.src.entities.item import Item
from packages.intake.src.folder_scanner import FolderScanner
from packages.classification.src.category_mapper import CategoryMapper

console = Console()


def run_backfill(source_path: Path, dry_run: bool = False) -> None:
    console.rule("[bold]Resale AI — Backfill Migration[/bold]")
    console.print(f"Source: [cyan]{source_path}[/cyan]")
    console.print(f"Dry run: [yellow]{dry_run}[/yellow]\n")

    if not source_path.exists():
        console.print(f"[red]Error: source path does not exist: {source_path}[/red]")
        sys.exit(1)

    # Init DB
    init_db()

    scanner = FolderScanner()
    mapper = CategoryMapper()

    console.print("Scanning folders...")
    manifests = scanner.scan_existing(source_path)

    if not manifests:
        console.print("[yellow]No SKU folders found. Check your source path.[/yellow]")
        sys.exit(0)

    console.print(f"Found [green]{len(manifests)}[/green] item folders.\n")

    # Summary table
    table = Table(title="Folders found", show_header=True, header_style="bold")
    table.add_column("SKU", style="cyan")
    table.add_column("Images", justify="right")
    table.add_column("Category")
    table.add_column("Valid")
    for m in manifests[:20]:
        cat = mapper.from_prefix(m.detected_prefix or "").value or {}
        table.add_row(
            m.detected_sku or m.folder_name,
            str(m.image_count),
            cat.get("category_label", "?"),
            "[green]yes[/green]" if m.is_valid else "[red]no[/red]",
        )
    if len(manifests) > 20:
        table.add_row(f"... and {len(manifests) - 20} more", "", "", "")
    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run — no changes written.[/yellow]")
        return

    # Collect all existing SKUs for registry init
    existing_skus = [m.detected_sku for m in manifests if m.detected_sku]

    stats = {"created": 0, "skipped": 0, "failed": 0}
    batch_id = str(uuid.uuid4())

    with Session(engine) as session:
        item_repo = ItemRepository(session)
        sku_repo = SKURepository(session)

        # Initialise registry from all existing SKUs — prevents future collisions
        highest = sku_repo.initialize_from_existing_folders(existing_skus)
        console.print("\nSKU registry initialised:")
        for prefix, num in sorted(highest.items()):
            console.print(f"  {prefix}: last number = {num:06d}")

        # Create batch record
        batch = BatchRecord(
            batch_id=batch_id,
            batch_name=f"backfill_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            source_path=str(source_path),
            item_count=len(manifests),
            status="running",
        )
        session.add(batch)
        session.commit()

        console.print(f"\nProcessing {len(manifests)} items...\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Importing...", total=len(manifests))

            for manifest in manifests:
                progress.advance(task)

                if not manifest.is_valid:
                    console.print(f"  [yellow]Skip {manifest.folder_name}: {manifest.errors}[/yellow]")
                    stats["skipped"] += 1
                    continue

                try:
                    sku = manifest.detected_sku

                    # Check if already in DB — skip without touching
                    existing = item_repo.get_by_sku(sku) if sku else None
                    if existing:
                        stats["skipped"] += 1
                        continue

                    # Derive category from prefix
                    cat_result = mapper.from_prefix(manifest.detected_prefix or "")
                    cat_data = cat_result.value if cat_result.ok else {}

                    # Build image paths (absolute)
                    image_paths = [str(p) for p in manifest.image_paths]

                    item = Item(
                        sku=sku,
                        status=ItemStatus.PENDING_INTAKE,
                        item_mode=ItemMode.SINGLE,
                        batch_id=batch_id,
                        photo_folder=str(manifest.folder_path),
                        image_paths=image_paths,
                        category_key=cat_data.get("category_key"),
                        category_label=cat_data.get("category_label"),
                        ebay_category_id=cat_data.get("ebay_category_id"),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )

                    item_repo.upsert(item)
                    stats["created"] += 1

                except Exception as e:
                    console.print(f"  [red]Error {manifest.folder_name}: {e}[/red]")
                    stats["failed"] += 1

        # Update batch record
        batch.processed_count = stats["created"]
        batch.failed_count = stats["failed"]
        batch.status = "complete"
        batch.finished_at = datetime.utcnow()
        session.add(batch)
        session.commit()

    # Final summary
    console.print()
    console.rule("Migration complete")
    console.print(f"  [green]Created : {stats['created']}[/green]")
    console.print(f"  [yellow]Skipped : {stats['skipped']}[/yellow]  (already in DB or no images)")
    console.print(f"  [red]Failed  : {stats['failed']}[/red]")
    console.print()
    console.print("Next step: run AI analysis on imported items.")
    console.print("  [cyan]uv run python apps/worker/src/main.py[/cyan]")
    console.print("  or open [cyan]http://localhost:8000[/cyan] → Intake Queue")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill existing inventory into the database.")
    parser.add_argument(
        "--source", required=True,
        help="Path to your existing Inventory_Photos folder (e.g. C:\\Inventory_Photos)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report without writing anything to the database."
    )
    args = parser.parse_args()
    run_backfill(Path(args.source), dry_run=args.dry_run)
