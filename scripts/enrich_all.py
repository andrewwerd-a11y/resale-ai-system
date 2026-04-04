"""
enrich_all.py — run Claude API enrichment on approved/needs_review items.

Skips items that already have enrichment_done=True.
Cost estimate: ~$0.02 per item at claude-sonnet-4 pricing.

Usage:
    uv run python scripts/enrich_all.py
    uv run python scripts/enrich_all.py --limit 10
    uv run python scripts/enrich_all.py --sku CL-000001
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from sqlalchemy import or_
from sqlmodel import Session, select

from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.enrichment.src.enricher import ItemEnricher

console = Console()


def enrich_all(limit: int | None = None, sku_filter: str | None = None) -> None:
    init_db()
    console.rule("[bold]Resale AI — Claude Enrichment Pass[/bold]")

    enricher = ItemEnricher()
    if not enricher.is_available():
        console.print(
            "[red]Enrichment not available.[/red] "
            "Set [cyan]ANTHROPIC_API_KEY[/cyan] and [cyan]ENRICHMENT_ENABLED=true[/cyan] in .env"
        )
        sys.exit(1)

    console.print(f"Model: [cyan]{enricher.settings.enrichment_model}[/cyan]\n")

    # Collect candidates: approved, needs_review, export_ready, or exported — not yet enriched
    eligible_statuses = ["approved", "needs_review", "export_ready", "exported"]
    with Session(engine) as session:
        stmt = select(ItemRecord).where(
            ItemRecord.status.in_(eligible_statuses),
            or_(
                ItemRecord.enrichment_done == False,  # noqa: E712
                ItemRecord.enrichment_done == None,   # noqa: E711
            ),
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
        console.print("[yellow]No items to enrich — all done or none eligible.[/yellow]")
        return

    est_cost = len(skus) * 0.02
    console.print(
        f"Enriching [green]{len(skus)}[/green] items "
        f"([dim]~${est_cost:.2f} estimated[/dim])\n"
    )

    stats: dict[str, int] = {"enriched": 0, "skipped": 0, "failed": 0}
    total_cost = 0.0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching...", total=len(skus))

        for sku in skus:
            progress.update(task, description=f"{sku}...")

            try:
                # Per-item session — one failure never cascades
                with Session(engine) as session:
                    repo = ItemRepository(session)
                    item = repo.get_by_sku(sku)
                    if not item:
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    result = enricher.enrich(item)
                    if not result.ok:
                        console.print(f"  [red]FAIL {sku}: {result.error}[/red]")
                        stats["failed"] += 1
                        progress.advance(task)
                        continue

                    enriched_item = enricher.apply_to_item(item, result.value)
                    enriched_item.updated_at = datetime.utcnow()
                    repo.upsert(enriched_item)

                    cost = result.details.get("estimated_cost", 0.0)
                    total_cost += cost
                    stats["enriched"] += 1

            except Exception as e:
                console.print(f"  [red]ERROR {sku}: {e}[/red]")
                stats["failed"] += 1

            progress.advance(task)

    console.rule("Complete")
    console.print(f"  [green]Enriched : {stats['enriched']}[/green]")
    console.print(f"  [yellow]Skipped  : {stats['skipped']}[/yellow]")
    console.print(f"  [red]Failed   : {stats['failed']}[/red]")
    console.print(f"  [dim]Est. cost: ~${total_cost:.4f}[/dim]\n")
    if stats["enriched"] > 0:
        console.print(
            "Open [cyan]http://localhost:8000/review-queue[/cyan] "
            "to review enriched items."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude API enrichment pass")
    parser.add_argument("--limit", type=int, default=None, help="Max items to process")
    parser.add_argument("--sku", type=str, default=None, help="Target a specific SKU or prefix")
    args = parser.parse_args()
    enrich_all(limit=args.limit, sku_filter=args.sku)
