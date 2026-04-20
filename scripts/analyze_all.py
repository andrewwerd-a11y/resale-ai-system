"""
analyze_all.py — run AI analysis on all pending_intake items in the database.

Usage:
    uv run python scripts/analyze_all.py
    uv run python scripts/analyze_all.py --limit 10
    uv run python scripts/analyze_all.py --sku CL
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from sqlmodel import Session

from packages.core.src.constants import ItemStatus, ItemMode
from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.models.review_record import ReviewRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.category_intelligence import CategoryIntelligence
from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
from packages.intake.src.image_normalizer import ImageNormalizer
from packages.pricing.src.estimator import PriceEstimator
from packages.triage.src.router import TriageRouter
from packages.vision.src.ollama_provider import OllamaProvider
from packages.vision.src.prompt_builder import build_extraction_prompt
from packages.vision.src.response_parser import ResponseParser

console = Console()


def _safe_list(val) -> list:
    """Ensure value is a flat list of strings — never a list of dicts."""
    if not val:
        return []
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if not isinstance(v, dict)]
        except Exception:
            return [val]
    if isinstance(val, list):
        return [str(v) for v in val if not isinstance(v, dict)]
    return []


def analyze_all(limit: int | None = None, prefix_filter: str | None = None) -> None:
    init_db()
    console.rule("[bold]Resale AI — Analyze All Pending Items[/bold]")

    provider = OllamaProvider()
    if not provider.is_available():
        console.print("[red]Ollama is not running.[/red]")
        sys.exit(1)

    console.print(f"Model: [cyan]{provider.model_id}[/cyan]\n")

    parser = ResponseParser()
    router = TriageRouter()
    normalizer = ImageNormalizer()
    estimator = PriceEstimator()
    cat_intel = CategoryIntelligence()
    cat_sheet = CategorySpreadsheet()

    with Session(engine) as session:
        repo = ItemRepository(session)
        all_items = repo.list_by_status(ItemStatus.PENDING_INTAKE)
        if prefix_filter:
            all_items = [i for i in all_items if (i.sku or "").startswith(prefix_filter.upper())]
        if limit:
            all_items = all_items[:limit]
        pending_skus = [i.sku for i in all_items if i.sku]

    if not pending_skus:
        console.print("[yellow]No pending_intake items found.[/yellow]")
        return

    console.print(f"Processing [green]{len(pending_skus)}[/green] items...\n")
    stats = {"approved": 0, "review": 0, "rejected": 0, "failed": 0, "skipped": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=len(pending_skus))

        for sku in pending_skus:
            progress.update(task, description=f"{sku}...")

            try:
                with Session(engine) as session:
                    repo = ItemRepository(session)
                    item = repo.get_by_sku(sku)
                    if not item:
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    image_paths = [Path(p) for p in (item.image_paths or []) if Path(p).exists()]
                    if not image_paths:
                        console.print(f"  [yellow]Skip {sku}: images not found[/yellow]")
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    norm_result = normalizer.normalize_folder(Path(item.photo_folder))
                    if norm_result.ok:
                        image_paths = norm_result.value

                    prompt = build_extraction_prompt(item.category_key or "clothing")
                    vision_result = provider.analyze(image_paths=image_paths, prompt=prompt)
                    if not vision_result.ok:
                        console.print(f"  [red]FAIL {sku}: {vision_result.error}[/red]")
                        stats["failed"] += 1
                        progress.advance(task)
                        continue

                    parse_result = parser.parse(vision_result.value, item.category_key or "clothing")
                    if not parse_result.ok:
                        console.print(f"  [red]FAIL {sku}: parse error[/red]")
                        stats["failed"] += 1
                        progress.advance(task)
                        continue

                    extracted = parse_result.value

                    skip_fields = {
                        "sku", "status", "batch_id", "photo_folder", "image_paths",
                        "category_key", "category_label", "ebay_category_id",
                        "cost", "storage_location",
                    }
                    if not item.manual_override:
                        for k, v in extracted.items():
                            if k in skip_fields or not hasattr(item, k):
                                continue
                            if isinstance(v, list):
                                v = _safe_list(v)
                            setattr(item, k, v)

                    item.features = _safe_list(item.features)
                    item.defects = _safe_list(item.defects)
                    item.review_reasons = _safe_list(item.review_reasons)

                    # ── Step 2: Category Intelligence ──────────────────────────
                    cat_id = cat_intel.get_category_id(item)
                    cat_result = cat_intel.get_template(cat_id)
                    if cat_result.ok:
                        template = cat_result.value
                        item.ebay_category_id = cat_id
                        item.ebay_category_name = template.category_name
                        item.category_template_fetched = True
                        item.category_template_fetched_at = datetime.utcnow().isoformat()
                        cat_sheet.save_template(template)
                        validation = cat_intel.validate_item_specifics(item, template)
                        item.missing_required_fields = validation.missing_required
                        item.missing_recommended_fields = validation.missing_recommended
                        item.publish_ready = validation.is_publish_ready
                        if validation.missing_required:
                            if "missing_required_specifics" not in item.review_reasons:
                                item.review_reasons.append("missing_required_specifics")
                    else:
                        logger.warning("Category intelligence unavailable for %s: %s", sku, cat_result.error)

                    item = estimator.apply(item)

                    triage = router.route(item)
                    item.item_mode = triage.item_mode
                    item.needs_review = triage.needs_review
                    combined = item.review_reasons + _safe_list(triage.review_reasons)
                    seen = []
                    for r in combined:
                        if r not in seen:
                            seen.append(r)
                    item.review_reasons = seen

                    if triage.needs_review or triage.item_mode == ItemMode.REVIEW:
                        item.status = ItemStatus.NEEDS_REVIEW
                        stats["review"] += 1
                    elif triage.item_mode == ItemMode.REJECT:
                        item.status = ItemStatus.REJECTED
                        stats["rejected"] += 1
                    else:
                        item.status = ItemStatus.APPROVED
                        stats["approved"] += 1

                    item.updated_at = datetime.utcnow()
                    repo.upsert(item)

                    if item.needs_review and item.review_reasons:
                        review = ReviewRecord(
                            sku=sku,
                            trigger_reason=json.dumps(item.review_reasons),
                            confidence_score=item.confidence_score,
                            high_value_flag=(item.estimated_price or 0) >= 75.0,
                        )
                        session.add(review)
                        session.commit()

            except Exception as e:
                console.print(f"  [red]ERROR {sku}: {e}[/red]")
                stats["failed"] += 1

            progress.advance(task)

    console.rule("Complete")
    console.print(f"  [green]Approved  : {stats['approved']}[/green]")
    console.print(f"  [yellow]Review    : {stats['review']}[/yellow]")
    console.print(f"  [dim]Rejected  : {stats['rejected']}[/dim]")
    console.print(f"  [yellow]Skipped   : {stats['skipped']}[/yellow]")
    console.print(f"  [red]Failed    : {stats['failed']}[/red]")
    console.print()
    if stats["review"] > 0:
        console.print("Open [cyan]http://localhost:8000/review-queue[/cyan] to approve flagged items.")
    if stats["approved"] > 0:
        console.print("Run [cyan]uv run python scripts/export_ebay_csv.py[/cyan] to generate your eBay CSV.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sku", type=str, default=None)
    args = parser.parse_args()
    analyze_all(limit=args.limit, prefix_filter=args.sku)
