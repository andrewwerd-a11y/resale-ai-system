"""
run_category_intelligence.py — fetch eBay category templates for all items.

Queries the eBay Taxonomy API to populate required/recommended item specifics
for every item in the database, regardless of status.

Skips items where category_template_fetched = True unless --reset is passed.

Usage:
    uv run python scripts/run_category_intelligence.py
    uv run python scripts/run_category_intelligence.py --limit 20
    uv run python scripts/run_category_intelligence.py --sku CL-000001
    uv run python scripts/run_category_intelligence.py --reset
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from sqlmodel import Session, select

from packages.data.src.db.sqlite import engine, init_db, migrate_add_columns
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.category_intelligence import CategoryIntelligence
from packages.ebay.src.category_spreadsheet import CategorySpreadsheet

console = Console()


def run_category_intelligence(
    limit: int | None = None,
    sku_filter: str | None = None,
    reset: bool = False,
) -> None:
    init_db()
    migrate_add_columns()
    console.rule("[bold]Resale AI — Category Intelligence Pass[/bold]")

    cat_intel = CategoryIntelligence()
    cat_sheet = CategorySpreadsheet()

    # Collect candidates — all items, any status
    with Session(engine) as session:
        stmt = select(ItemRecord)
        if not reset:
            stmt = stmt.where(
                (ItemRecord.category_template_fetched == 0)
                | (ItemRecord.category_template_fetched == None)  # noqa: E711
            )
        candidates = list(session.exec(stmt).all())

    if sku_filter:
        candidates = [
            r for r in candidates
            if r.sku == sku_filter or (r.sku or "").startswith(sku_filter.upper())
        ]
    if limit:
        candidates = candidates[:limit]

    skus = [r.sku for r in candidates if r.sku]

    if not skus:
        msg = "No items to process"
        if not reset:
            msg += " — all templates already fetched (use --reset to re-run)"
        console.print(f"[yellow]{msg}.[/yellow]")
        return

    mode = "[yellow]RESET — re-fetching all[/yellow]" if reset else "skipping already-fetched"
    console.print(f"Processing [green]{len(skus)}[/green] items ({mode})\n")

    stats: dict[str, int] = {"updated": 0, "skipped": 0, "failed": 0}
    category_hits: dict[str, int] = {}   # category_id → item count

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching templates...", total=len(skus))

        for sku in skus:
            progress.update(task, description=f"{sku}...")

            try:
                with Session(engine) as session:
                    repo = ItemRepository(session)
                    item = repo.get_by_sku(sku)
                    if not item:
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    # On --reset, ignore any stale stored category ID so
                    # get_category_id re-derives it from category_key + title
                    if reset:
                        item.ebay_category_id = None
                    cat_id = cat_intel.get_category_id(item)
                    result = cat_intel.get_template(cat_id)

                    if not result.ok:
                        console.print(
                            f"  [yellow]SKIP {sku}: {result.error}[/yellow]"
                        )
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    template = result.value
                    cat_sheet.save_template(template)
                    cat_sheet.update_field_stats(cat_id, item)

                    # Update item fields
                    item.ebay_category_id = cat_id
                    item.ebay_category_name = template.category_name
                    item.category_template_fetched = True
                    item.category_template_fetched_at = datetime.utcnow().isoformat()

                    validation = cat_intel.validate_item_specifics(item, template)
                    item.missing_required_fields = validation.missing_required
                    item.missing_recommended_fields = validation.missing_recommended
                    item.publish_ready = validation.is_publish_ready

                    # Add review reason if required fields are missing
                    review_reasons = list(item.review_reasons or [])
                    if validation.missing_required:
                        if "missing_required_specifics" not in review_reasons:
                            review_reasons.append("missing_required_specifics")
                    else:
                        if "missing_required_specifics" in review_reasons:
                            review_reasons.remove("missing_required_specifics")
                    item.review_reasons = review_reasons

                    item.updated_at = datetime.utcnow()
                    repo.upsert(item)

                    category_hits[cat_id] = category_hits.get(cat_id, 0) + 1
                    stats["updated"] += 1

            except Exception as e:
                console.print(f"  [red]ERROR {sku}: {e}[/red]")
                stats["failed"] += 1

            progress.advance(task)

    console.rule("Complete")
    console.print(f"  [green]Updated  : {stats['updated']}[/green]")
    console.print(f"  [yellow]Skipped  : {stats['skipped']}[/yellow]")
    console.print(f"  [red]Failed   : {stats['failed']}[/red]")

    if category_hits:
        console.print("\n[dim]Categories seen:[/dim]")
        for cat_id, count in sorted(category_hits.items(), key=lambda x: -x[1]):
            console.print(f"  [dim]{cat_id}: {count} item{'s' if count != 1 else ''}[/dim]")

    console.print()
    if stats["updated"] > 0:
        console.print(
            "Open [cyan]http://localhost:8000/reports[/cyan] for category intelligence summary."
        )
        console.print(
            "Items with missing required fields are flagged in "
            "[cyan]http://localhost:8000/review-queue[/cyan]."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch eBay category templates for all items"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max items to process")
    parser.add_argument(
        "--sku", type=str, default=None, help="Target a specific SKU or prefix"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Re-fetch templates even for items already processed",
    )
    args = parser.parse_args()
    run_category_intelligence(
        limit=args.limit,
        sku_filter=args.sku,
        reset=args.reset,
    )
