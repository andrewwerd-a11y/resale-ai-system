"""
Reset all items back to pending_intake status.
WARNING: This clears all AI analysis data.

Usage:
  uv run python scripts/reset_pending.py --confirm
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository


def main():
    parser = argparse.ArgumentParser(description="Reset all items to pending_intake")
    parser.add_argument("--confirm", action="store_true", required=True,
                        help="Required: confirm you want to reset all items")
    args = parser.parse_args()

    init_db()

    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_all()

    count = 0
    for item in items:
        if item.manual_override:
            print(f"  [SKIP] {item.sku} — manual_override=True, skipping")
            continue
        reset = item.model_copy(update={
            "status": "pending_intake",
            "ai_confidence": None,
            "title": None,
            "brand": None,
            "condition": None,
            "review_reasons": [],
            "raw_ai_response": None,
        })
        with get_session() as session:
            repo = ItemRepository(session)
            repo.upsert(reset)
        count += 1

    print(f"Reset {count} items to pending_intake.")


if __name__ == "__main__":
    main()
