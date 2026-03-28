"""
Generate eBay bulk listing CSV.

Usage:
  uv run python scripts/export_ebay_csv.py
  uv run python scripts/export_ebay_csv.py --all
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.csv_writer import generate_ebay_csv


def main():
    parser = argparse.ArgumentParser(description="Export eBay bulk CSV")
    parser.add_argument("--all", action="store_true", help="Include all non-rejected items")
    args = parser.parse_args()

    init_db()

    with get_session() as session:
        repo = ItemRepository(session)
        if args.all:
            items = [i for i in repo.list_all() if i.status not in ("rejected", "archived")]
        else:
            items = repo.list_by_statuses(["approved", "export_ready", "exported"])

    if not items:
        print("No items to export.")
        return

    output = generate_ebay_csv(items)
    print(f"Exported {len(items)} items to: {output}")


if __name__ == "__main__":
    main()
