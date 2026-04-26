"""
Worker — processes items in intake/pending/ through the full pipeline:
  scan → normalise images → AI vision → parse → triage → write to DB

Run with:
    uv run python apps/worker/src/main.py

Processes all pending items, then exits. Run again for new batches.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from sqlmodel import Session

from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus, ItemMode
from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.models.batch_record import BatchRecord
from packages.data.src.models.review_record import ReviewRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.data.src.repositories.sku_repo import SKURepository
from packages.domain.src.entities.item import Item
from packages.intake.src.folder_scanner import FolderScanner
from packages.intake.src.image_normalizer import ImageNormalizer
from packages.vision.src.ollama_provider import OllamaProvider
from packages.vision.src.prompt_builder import build_extraction_prompt
from packages.vision.src.response_parser import ResponseParser
from packages.classification.src.category_mapper import CategoryMapper
from packages.triage.src.router import TriageRouter

console = Console()


def process_item(
    manifest,
    session: Session,
    provider: OllamaProvider,
    parser: ResponseParser,
    mapper: CategoryMapper,
    router: TriageRouter,
    normalizer: ImageNormalizer,
    batch_id: str,
) -> tuple[bool, str]:
    """Process one item folder end-to-end. Returns (success, message)."""

    sku = manifest.detected_sku
    if not sku:
        return False, "no_sku_detected"

    item_repo = ItemRepository(session)

    # Check if already fully processed
    existing = item_repo.get_by_sku(sku)
    if existing and existing.status not in (ItemStatus.PENDING_INTAKE, ItemStatus.SKU_CONFIRMED):
        return True, f"already_processed_status={existing.status}"

    # Normalise images
    norm_result = normalizer.normalize_folder(manifest.folder_path)
    if not norm_result.ok:
        return False, f"image_normalisation_failed: {norm_result.error}"
    image_paths = [str(p) for p in norm_result.value]

    # Derive category from SKU prefix
    cat_result = mapper.from_prefix(manifest.detected_prefix or "")
    if not cat_result.ok:
        return False, f"category_mapping_failed: {cat_result.error}"
    cat_data = cat_result.value
    category_key = cat_data["category_key"]

    # Build prompt and run vision analysis
    prompt = build_extraction_prompt(category_key)
    vision_result = provider.analyze(
        image_paths=[Path(p) for p in image_paths],
        prompt=prompt,
    )
    if not vision_result.ok:
        return False, f"vision_failed: {vision_result.error}"

    # Parse and validate extracted data
    parse_result = parser.parse(vision_result.value, category_key)
    if not parse_result.ok:
        return False, f"parse_failed: {parse_result.error}"
    extracted = parse_result.value

    # Build item entity
    item = Item(
        sku=sku,
        status=ItemStatus.ANALYZED,
        batch_id=batch_id,
        photo_folder=str(manifest.folder_path),
        image_paths=image_paths,
        category_key=category_key,
        category_label=cat_data.get("category_label"),
        ebay_category_id=cat_data.get("ebay_category_id"),
        **{k: v for k, v in extracted.items()
           if k in Item.model_fields and k not in ("sku", "status", "batch_id",
                                                    "photo_folder", "image_paths",
                                                    "category_key", "category_label",
                                                    "ebay_category_id")},
    )

    # Triage
    triage = router.route(item)
    item.item_mode = triage.item_mode
    item.needs_review = triage.needs_review
    if triage.review_reasons:
        item.review_reasons = list(set((item.review_reasons or []) + triage.review_reasons))

    # Set final status
    if triage.needs_review or triage.item_mode == ItemMode.REVIEW:
        item.status = ItemStatus.NEEDS_REVIEW
    elif triage.item_mode == ItemMode.REJECT:
        item.status = ItemStatus.REJECTED
    else:
        item.status = ItemStatus.APPROVED

    # Write to DB
    item_repo.upsert(item)

    # Create review case if needed
    if item.needs_review and item.review_reasons:
        review = ReviewRecord(
            sku=sku,
            trigger_reason=str(item.review_reasons),
            confidence_score=item.confidence_score,
            missing_fields=str([]),
            high_value_flag=(item.estimated_price or 0) >= 75.0,
        )
        session.add(review)
        session.commit()

    return True, f"status={item.status} mode={item.item_mode}"


def _process_manifests(manifests: list, batch_name: str) -> dict[str, object]:
    settings = get_settings()
    provider = OllamaProvider()
    if not settings.dry_run and not provider.is_available():
        return {
            "ok": False,
            "error": "ollama_unavailable",
            "message": "Ollama is not running and DRY_RUN is false.",
            "requested_skus": [],
            "found_skus": [],
            "missing_skus": [],
            "processed_count": 0,
            "approved_count": 0,
            "review_count": 0,
            "rejected_count": 0,
            "failed_count": 0,
        }

    normalizer = ImageNormalizer()
    parser = ResponseParser()
    mapper = CategoryMapper()
    router = TriageRouter()

    batch_id = str(uuid.uuid4())
    stats = {"ok": 0, "review": 0, "rejected": 0, "failed": 0}

    with Session(engine) as session:
        batch = BatchRecord(
            batch_id=batch_id,
            batch_name=batch_name,
            item_count=len(manifests),
            status="running",
        )
        session.add(batch)
        session.commit()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processing...", total=len(manifests))

            for manifest in manifests:
                desc = manifest.detected_sku or manifest.folder_name
                progress.update(task, description=f"Processing {desc}...")

                try:
                    ok, msg = process_item(
                        manifest, session, provider, parser,
                        mapper, router, normalizer, batch_id,
                    )
                    if ok:
                        if "review" in msg:
                            stats["review"] += 1
                        elif "rejected" in msg:
                            stats["rejected"] += 1
                        else:
                            stats["ok"] += 1
                    else:
                        stats["failed"] += 1
                        console.print(f"  [red]FAIL {desc}: {msg}[/red]")
                except Exception as exc:  # noqa: BLE001
                    stats["failed"] += 1
                    console.print(f"  [red]ERROR {desc}: {exc}[/red]")

                progress.advance(task)

        batch.processed_count = stats["ok"] + stats["review"] + stats["rejected"]
        batch.failed_count = stats["failed"]
        batch.status = "complete"
        batch.finished_at = datetime.utcnow()
        session.add(batch)
        session.commit()

    return {
        "ok": True,
        "error": None,
        "message": "processing_complete",
        "requested_skus": [],
        "found_skus": [],
        "missing_skus": [],
        "processed_count": stats["ok"] + stats["review"] + stats["rejected"],
        "approved_count": stats["ok"],
        "review_count": stats["review"],
        "rejected_count": stats["rejected"],
        "failed_count": stats["failed"],
    }


def run_worker_for_skus(requested_skus: list[str]) -> dict[str, object]:
    """
    Process only matching SKU folders from intake/pending.
    This is intended for constrained E2E-safe execution paths.
    """
    init_db()
    scanner = FolderScanner()
    manifests = scanner.scan_pending()

    normalized_requested = sorted({(sku or "").strip().upper() for sku in requested_skus if sku})
    by_sku = {
        (m.detected_sku or "").upper(): m
        for m in manifests
        if m.detected_sku
    }
    selected = [by_sku[sku] for sku in normalized_requested if sku in by_sku]
    found = sorted([(m.detected_sku or "").upper() for m in selected if m.detected_sku])
    missing = [sku for sku in normalized_requested if sku not in set(found)]

    if not selected:
        return {
            "ok": True,
            "error": None,
            "message": "no_matching_pending_folders",
            "requested_skus": normalized_requested,
            "found_skus": found,
            "missing_skus": missing,
            "processed_count": 0,
            "approved_count": 0,
            "review_count": 0,
            "rejected_count": 0,
            "failed_count": 0,
        }

    result = _process_manifests(
        selected,
        batch_name=f"worker_constrained_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
    )
    result["requested_skus"] = normalized_requested
    result["found_skus"] = found
    result["missing_skus"] = missing
    return result


def run_worker() -> None:
    settings = get_settings()
    init_db()

    console.rule("[bold]Resale AI — Processing Worker[/bold]")

    # Check Ollama availability
    provider = OllamaProvider()
    if not settings.dry_run and not provider.is_available():
        console.print("[red]Ollama is not running.[/red]")
        console.print("Start it with: [cyan]ollama serve[/cyan]")
        console.print("Or set DRY_RUN=true in .env to test without it.")
        sys.exit(1)

    console.print(f"Model: [cyan]{provider.model_id}[/cyan]")
    console.print(f"Dry run: [yellow]{settings.dry_run}[/yellow]\n")

    scanner = FolderScanner()
    manifests = scanner.scan_pending()
    if not manifests:
        console.print("[yellow]No items in intake/pending/ — nothing to process.[/yellow]")
        console.print(f"Add item folders to: [cyan]{settings.intake_root / 'pending'}[/cyan]")
        return

    console.print(f"Found [green]{len(manifests)}[/green] items to process.\n")
    result = _process_manifests(
        manifests,
        batch_name=f"worker_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
    )
    stats = {
        "ok": int(result.get("approved_count", 0)),
        "review": int(result.get("review_count", 0)),
        "rejected": int(result.get("rejected_count", 0)),
        "failed": int(result.get("failed_count", 0)),
    }

    console.print()
    console.rule("Done")
    console.print(f"  [green]Approved : {stats['ok']}[/green]")
    console.print(f"  [yellow]Review   : {stats['review']}[/yellow]")
    console.print(f"  [dim]Rejected : {stats['rejected']}[/dim]")
    console.print(f"  [red]Failed   : {stats['failed']}[/red]")
    console.print()
    if stats["review"] > 0:
        console.print(f"  Open [cyan]http://localhost:8000[/cyan] → Review Queue to approve items.")


if __name__ == "__main__":
    run_worker()
