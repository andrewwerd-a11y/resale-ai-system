"""
Run AI vision analysis on all pending_intake items.

Usage:
  uv run python scripts/analyze_all.py
  uv run python scripts/analyze_all.py --limit 10
  uv run python scripts/analyze_all.py --sku BK-000001
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.vision.src.ollama_provider import OllamaProvider
from packages.vision.src.prompt_builder import build_extraction_prompt
from packages.vision.src.response_parser import parse_extraction_response
from packages.classification.src.category_mapper import map_ai_category
from packages.triage.src.router import triage_item
from packages.pricing.src.estimator import enrich_pricing
from packages.domain.src.entities.item import Item


def _safe_list(val) -> list[str]:
    """Ensure val is a flat list of strings (not dicts)."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val else []
    if isinstance(val, list):
        result = []
        for item in val:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, str) and v:
                        result.append(v)
            else:
                s = str(item)
                if s:
                    result.append(s)
        return list(dict.fromkeys(result))  # dedup preserving order
    return [str(val)]


def analyze_item(item: Item, provider: OllamaProvider) -> Item | None:
    """Run AI analysis on one item. Returns updated Item or None on failure."""
    if not item.image_paths:
        print(f"  [SKIP] {item.sku} — no images")
        return None

    # Use up to 4 images
    images = [p for p in item.image_paths if Path(p).exists()][:4]
    if not images:
        print(f"  [SKIP] {item.sku} — image files not found on disk")
        return None

    category_hint = item.category
    prompt = build_extraction_prompt(category_hint)

    result = provider.analyze(images, prompt)
    if result.is_err:
        print(f"  [FAIL] {item.sku} — AI error: {result.error}")
        return None

    parse_result = parse_extraction_response(result.value)
    if parse_result.is_err:
        print(f"  [FAIL] {item.sku} — Parse error: {parse_result.error}")
        return None

    data = parse_result.value
    category = map_ai_category(data.get("category") or category_hint)

    updates = {
        "title": data.get("title"),
        "category": category,
        "brand": data.get("brand"),
        "item_type": data.get("item_type"),
        "department": data.get("department"),
        "size": data.get("size"),
        "color": data.get("color"),
        "material": data.get("material"),
        "style": data.get("style"),
        "condition": data.get("condition"),
        "condition_id": data.get("condition_id"),
        "condition_notes": data.get("condition_notes"),
        "author": data.get("author"),
        "book_format": data.get("book_format"),
        "isbn": data.get("isbn"),
        "franchise": data.get("franchise"),
        "character": data.get("character"),
        "features": _safe_list(data.get("features")),
        "defects": _safe_list(data.get("defects")),
        "keywords": _safe_list(data.get("keywords")),
        "estimated_price": data.get("estimated_price"),
        "ai_confidence": data.get("ai_confidence"),
        "ai_model": "minicpm-v",
        "raw_ai_response": result.value,
        "status": "analyzed",
    }

    # Don't overwrite manual_override fields
    if item.manual_override:
        for key in list(updates.keys()):
            if getattr(item, key, None) is not None:
                del updates[key]

    analyzed = item.model_copy(update=updates)

    # Triage
    status, reasons = triage_item(analyzed)
    analyzed = analyzed.model_copy(update={"status": status, "review_reasons": reasons})

    # Pricing
    pricing = enrich_pricing(analyzed)
    analyzed = analyzed.model_copy(update=pricing)

    return analyzed


def main():
    parser = argparse.ArgumentParser(description="Run AI analysis on pending items")
    parser.add_argument("--limit", type=int, default=None, help="Max items to process")
    parser.add_argument("--sku", type=str, default=None, help="Analyze a specific SKU only")
    parser.add_argument("--reanalyze", action="store_true", help="Re-analyze already analyzed items too")
    args = parser.parse_args()

    init_db()
    provider = OllamaProvider(num_ctx=8192, timeout=600)

    with get_session() as session:
        repo = ItemRepository(session)
        if args.sku:
            item = repo.get_by_sku(args.sku.upper())
            items = [item] if item else []
        elif args.reanalyze:
            items = repo.list_by_statuses(["pending_intake", "analyzed", "needs_review"])
        else:
            items = repo.list_by_status("pending_intake")

    if not items:
        print("No items to analyze.")
        return

    if args.limit:
        items = items[:args.limit]

    print(f"Analyzing {len(items)} items...")
    success = 0
    failed = 0

    for item in items:
        print(f"\n→ {item.sku} ({item.category or 'unknown'}) — {len(item.image_paths)} images")
        updated = analyze_item(item, provider)
        if updated is None:
            failed += 1
            continue

        # Save in its own session (failures don't cascade)
        try:
            with get_session() as session:
                repo = ItemRepository(session)
                repo.upsert(updated)
            print(f"  [OK]   {updated.sku} — {updated.status} — conf:{updated.ai_confidence:.2f} — ${updated.estimated_price}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] {item.sku} — DB save error: {e}")
            failed += 1

    print(f"\nDone. Success: {success}, Failed: {failed}")


if __name__ == "__main__":
    main()
