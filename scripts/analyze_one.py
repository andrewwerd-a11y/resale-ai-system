"""
analyze_one.py — run AI analysis on a single item and print the result.
Use this to verify extraction quality before running the full batch.

Usage:
    uv run python scripts/analyze_one.py --sku CL-000001
    uv run python scripts/analyze_one.py --sku BK-000001
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlmodel import Session

from packages.core.src.constants import ItemStatus, ItemMode
from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.data.src.models.review_record import ReviewRecord
from packages.classification.src.category_mapper import CategoryMapper
from packages.intake.src.image_normalizer import ImageNormalizer
from packages.pricing.src.estimator import PriceEstimator
from packages.triage.src.router import TriageRouter
from packages.vision.src.ollama_provider import OllamaProvider
from packages.vision.src.prompt_builder import build_extraction_prompt
from packages.vision.src.response_parser import ResponseParser

console = Console()


def analyze_one(sku: str) -> None:
    init_db()
    console.rule(f"[bold]Analyzing {sku}[/bold]")

    with Session(engine) as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)
        if not item:
            console.print(f"[red]SKU {sku} not found in database.[/red]")
            console.print("Run the backfill script first.")
            sys.exit(1)

        console.print(f"Category : [cyan]{item.category_key}[/cyan]")
        console.print(f"Folder   : [cyan]{item.photo_folder}[/cyan]")
        console.print(f"Images   : [cyan]{len(item.image_paths)}[/cyan]")
        console.print()

        # Check Ollama
        provider = OllamaProvider()
        if not provider.is_available():
            console.print("[red]Ollama is not running.[/red]")
            console.print("Start it: [cyan]ollama serve[/cyan]")
            sys.exit(1)

        console.print(f"Model: [cyan]{provider.model_id}[/cyan]")
        console.print("Running vision analysis... (may take 30-90 seconds)\n")

        # Build prompt
        prompt = build_extraction_prompt(item.category_key or "clothing")

        # Normalize images
        normalizer = ImageNormalizer()
        folder = Path(item.photo_folder)
        norm_result = normalizer.normalize_folder(folder)
        if not norm_result.ok:
            # Fall back to stored paths
            image_paths = [Path(p) for p in item.image_paths]
        else:
            image_paths = norm_result.value

        # Run vision
        vision_result = provider.analyze(image_paths=image_paths, prompt=prompt)
        if not vision_result.ok:
            console.print(f"[red]Vision failed: {vision_result.error}[/red]")
            sys.exit(1)

        # Parse
        parser = ResponseParser()
        parse_result = parser.parse(vision_result.value, item.category_key or "clothing")
        if not parse_result.ok:
            console.print(f"[red]Parse failed: {parse_result.error}[/red]")
            sys.exit(1)

        extracted = parse_result.value

        # Show results
        console.print(Panel("[bold green]Extraction complete[/bold green]"))

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Field", style="dim", width=22)
        table.add_column("Value")

        key_fields = [
            ("Title", "title_final"),
            ("Brand", "brand"),
            ("Type", "type"),
            ("Department", "department"),
            ("Size", "size"),
            ("Color", "color"),
            ("Material", "material"),
            ("Condition", "condition_label"),
            ("Condition notes", "condition_notes"),
            ("Defects", "defects"),
            ("Est. price", "estimated_price"),
            ("List price", "list_price"),
            ("Confidence", "confidence_score"),
            ("Needs review", "needs_review"),
            ("Review reasons", "review_reasons"),
        ]

        for label, field in key_fields:
            val = extracted.get(field)
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val) if val else "-"
            elif isinstance(val, float) and field == "confidence_score":
                val = f"{val:.0%}"
            elif isinstance(val, float):
                val = f"${val:.2f}"
            elif val is None:
                val = "[dim]-[/dim]"
            table.add_row(label, str(val))

        console.print(table)
        console.print()

        # Triage
        from packages.domain.src.entities.item import Item as ItemEntity
        import uuid
        test_item = ItemEntity(
            sku=sku,
            category_key=item.category_key,
            **{k: v for k, v in extracted.items()
               if k in ItemEntity.model_fields and k != "category_key"},
        )
        router = TriageRouter()
        triage = router.route(test_item)
        console.print(f"Triage result: [bold cyan]{triage.item_mode}[/bold cyan]")
        if triage.review_reasons:
            console.print(f"Review reasons: {triage.review_reasons}")
        console.print()

        # Ask to save
        console.print("Save this result to the database? [y/n] ", end="")
        answer = input().strip().lower()
        if answer == "y":
            # Apply pricing
            estimator = PriceEstimator()
            for k, v in extracted.items():
                if hasattr(item, k) and not item.manual_override:
                    setattr(item, k, v)

            item = estimator.apply(item)
            item.item_mode = triage.item_mode
            item.needs_review = triage.needs_review
            item.review_reasons = triage.review_reasons or []

            if triage.needs_review or triage.item_mode == ItemMode.REVIEW:
                item.status = ItemStatus.NEEDS_REVIEW
            elif triage.item_mode == ItemMode.REJECT:
                item.status = ItemStatus.REJECTED
            else:
                item.status = ItemStatus.APPROVED

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

            console.print(f"[green]Saved. Status: {item.status}[/green]")
        else:
            console.print("[yellow]Not saved.[/yellow]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sku", required=True, help="SKU to analyze (e.g. CL-000001)")
    args = parser.parse_args()
    analyze_one(args.sku)
