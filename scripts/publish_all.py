"""
publish_all.py — publish all export_ready items to eBay.

Fetches every item with status="export_ready", pushes each through the
Inventory API (PUT inventory_item → POST offer → POST publish), then
updates status to "listed" on success.

Usage:
    uv run python scripts/publish_all.py
    uv run python scripts/publish_all.py --limit 5
    uv run python scripts/publish_all.py --sku CL-000042
    uv run python scripts/publish_all.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from sqlmodel import Session, select

from packages.core.src.constants import ItemStatus
from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.inventory_client import EbayInventoryClient
from apps.api.src.services.publish_repair import get_publish_repair_blocker

console = Console()


def format_repair_blocker_for_console(blocker: dict) -> str:
    repair_status = blocker.get("repair_status") or {}
    parts = [
        "blocked_by_repair_queue",
        f"repair_plan_id={blocker.get('repair_plan_id') or ''}",
        f"retry_allowed={blocker.get('retry_allowed')}",
        f"repair_status={repair_status.get('status') or blocker.get('status') or ''}",
        f"classified_error_code={blocker.get('classified_error_code') or ''}",
    ]
    reason = str(blocker.get("reason") or "").strip()
    if reason:
        parts.append(f"reason={reason}")
    return " | ".join(parts)


def publish_all(
    limit: int | None = None,
    sku_filter: str | None = None,
    dry_run: bool = False,
) -> None:
    init_db()
    console.rule("[bold]Resale AI — eBay Publish Run[/bold]")

    client = EbayInventoryClient()
    if not client.auth.is_configured():
        console.print(
            "[red]eBay credentials not configured.[/red] "
            "Check [cyan]EBAY_PROD_*[/cyan] (or sandbox) keys and token in .env"
        )
        sys.exit(1)

    env = client.auth.settings.ebay_environment
    console.print(f"Environment : [cyan]{env}[/cyan]")
    if dry_run:
        console.print("[yellow]Dry-run mode — no items will actually be published.[/yellow]\n")

    # Collect candidates
    with Session(engine) as session:
        stmt = select(ItemRecord).where(ItemRecord.status == ItemStatus.EXPORT_READY)
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
        console.print("[yellow]No export_ready items found — nothing to publish.[/yellow]")
        return

    console.print(f"Publishing  : [green]{len(skus)}[/green] item(s)\n")

    stats: dict[str, int] = {"published": 0, "failed": 0, "skipped": 0}
    errors: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Publishing...", total=len(skus))

        for sku in skus:
            progress.update(task, description=f"{sku}...")

            try:
                with Session(engine) as session:
                    repo = ItemRepository(session)
                    item = repo.get_by_sku(sku)
                    if not item:
                        errors.append(f"{sku}: item not found in DB")
                        stats["failed"] += 1
                        progress.advance(task)
                        continue

                    if dry_run:
                        console.print(
                            f"  [dim]DRY-RUN {sku} — "
                            f"{(item.title_final or item.title_raw or '')[:60]}[/dim]"
                        )
                        stats["published"] += 1
                        progress.advance(task)
                        continue

                    repair_blocker = get_publish_repair_blocker(session, sku)
                    if repair_blocker["blocked_by_repair_queue"]:
                        detail = format_repair_blocker_for_console(repair_blocker)
                        console.print(f"  [yellow]SKIP[/yellow] {sku}: {detail}")
                        errors.append(f"{sku}: {detail}")
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    result = client.publish_item(item)

                    if result.ok:
                        data = result.value
                        item.listing_id  = data["listing_id"]
                        item.listing_url = data["listing_url"]
                        item.status      = ItemStatus.LISTED
                        item.platform    = "ebay"
                        item.date_listed = datetime.utcnow()
                        if data["photo_urls"]:
                            item.image_paths = data["photo_urls"]
                        repo.upsert(item)

                        console.print(
                            f"  [green]OK[/green] {sku} -> "
                            f"[link={data['listing_url']}]{data['listing_id']}[/link]"
                        )
                        stats["published"] += 1
                    else:
                        detail = result.error or "unknown error"
                        if result.details.get("body"):
                            detail += f" | eBay: {result.details['body']}"
                        console.print(f"  [red]FAIL[/red] {sku}: {detail}")
                        errors.append(f"{sku}: {detail}")
                        stats["failed"] += 1

            except Exception as exc:
                msg = f"{sku}: {exc}"
                console.print(f"  [red]ERROR[/red] {msg}")
                errors.append(msg)
                stats["failed"] += 1

            progress.advance(task)

    console.rule("Complete")
    console.print(f"  [green]Published : {stats['published']}[/green]")
    console.print(f"  [yellow]Skipped   : {stats['skipped']}[/yellow]")
    console.print(f"  [red]Failed    : {stats['failed']}[/red]")
    if errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for e in errors:
            console.print(f"  [red]•[/red] {e}")
    if stats["published"] > 0 and not dry_run:
        console.print(
            "\nView listings at [cyan]http://localhost:8000/inventory?status=listed[/cyan]"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish export_ready items to eBay")
    parser.add_argument("--limit",   type=int,  default=None,  help="Max items to publish")
    parser.add_argument("--sku",     type=str,  default=None,  help="Target a specific SKU or prefix")
    parser.add_argument("--dry-run", action="store_true",      help="Simulate without calling eBay API")
    args = parser.parse_args()
    publish_all(limit=args.limit, sku_filter=args.sku, dry_run=args.dry_run)
