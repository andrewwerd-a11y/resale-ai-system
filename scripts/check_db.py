"""
Print database status summary.

Usage:
  uv run python scripts/check_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.core.src.config import get_settings


def main():
    settings = get_settings()
    print(f"DB path:  {settings.db_path}")
    print(f"Exists:   {settings.db_path.exists()}")
    print()

    init_db()

    with get_session() as session:
        repo = ItemRepository(session)
        counts = repo.count_by_status()
        total = sum(counts.values())
        items = repo.list_all()

    print(f"Total items: {total}")
    print()
    print("Status breakdown:")
    for status, count in sorted(counts.items()):
        bar = "█" * min(count, 40)
        print(f"  {status:20s}  {count:4d}  {bar}")

    print()

    # Category breakdown
    cats: dict[str, int] = {}
    for item in items:
        cat = item.category or "Unknown"
        cats[cat] = cats.get(cat, 0) + 1

    print("Category breakdown:")
    for cat, count in sorted(cats.items()):
        print(f"  {cat:20s}  {count:4d}")

    print()

    # Show items with eBay listings
    listed = [i for i in items if i.ebay_listing_id]
    if listed:
        print(f"Listed on eBay: {len(listed)}")
        for i in listed[:10]:
            print(f"  {i.sku}  {i.ebay_listing_url}")

    # Show recent items
    recent = sorted(
        [i for i in items if i.date_created],
        key=lambda x: x.date_created or "",
        reverse=True,
    )[:10]
    if recent:
        print()
        print("Most recent items:")
        for i in recent:
            print(f"  {i.sku:12s}  {i.status:15s}  {i.title or 'No title'}")


if __name__ == "__main__":
    main()
