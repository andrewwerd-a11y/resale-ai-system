"""
Test AI vision analysis on a single item.

Usage:
  uv run python scripts/analyze_one.py --sku BK-000001
  uv run python scripts/analyze_one.py --sku BK-000001 --save
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.vision.src.ollama_provider import OllamaProvider
from packages.vision.src.prompt_builder import build_extraction_prompt
from packages.vision.src.response_parser import parse_extraction_response


def main():
    parser = argparse.ArgumentParser(description="Test AI on a single item")
    parser.add_argument("--sku", required=True, help="SKU to analyze")
    parser.add_argument("--save", action="store_true", help="Save results to DB")
    args = parser.parse_args()

    init_db()

    with get_session() as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku(args.sku.upper())

    if item is None:
        print(f"Item {args.sku} not found in database")
        sys.exit(1)

    print(f"Item: {item.sku}")
    print(f"Images: {item.image_paths}")
    print(f"Category hint: {item.category}")
    print()

    images = [p for p in item.image_paths if Path(p).exists()][:4]
    if not images:
        print("No image files found on disk.")
        sys.exit(1)

    provider = OllamaProvider(num_ctx=8192, timeout=600)
    prompt = build_extraction_prompt(item.category)

    print("Running AI analysis...")
    result = provider.analyze(images, prompt)

    if result.is_err:
        print(f"Error: {result.error}")
        sys.exit(1)

    print("\nRaw AI response:")
    print(result.value)
    print()

    parse_result = parse_extraction_response(result.value)
    if parse_result.is_err:
        print(f"Parse error: {parse_result.error}")
        sys.exit(1)

    print("Parsed data:")
    print(json.dumps(parse_result.value, indent=2))

    if args.save:
        from scripts.analyze_all import analyze_item
        updated = analyze_item(item, provider)
        if updated:
            with get_session() as session:
                repo = ItemRepository(session)
                repo.upsert(updated)
            print(f"\nSaved: {updated.status}, confidence: {updated.ai_confidence:.2f}")


if __name__ == "__main__":
    main()
