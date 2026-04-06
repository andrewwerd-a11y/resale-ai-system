"""
fix_item_modes.py — set item_mode = 'single' for items incorrectly marked 'review'
that have no lot_group_id (i.e. they are standalone items, not lot members).

Usage:
    uv run python scripts/fix_item_modes.py
    uv run python scripts/fix_item_modes.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from packages.data.src.db.sqlite import engine, init_db
from packages.data.src.models.item_record import ItemRecord


def fix_item_modes(dry_run: bool = False) -> None:
    init_db()

    with Session(engine) as session:
        stmt = select(ItemRecord).where(
            ItemRecord.item_mode == "review",
            ItemRecord.lot_group_id == None,  # noqa: E711
        )
        records = list(session.exec(stmt).all())

    if not records:
        print("No items to fix — all item_mode values look correct.")
        return

    print(f"Found {len(records)} item(s) with item_mode='review' and no lot_group_id.")

    if dry_run:
        print("Dry run — no changes written.")
        for r in records:
            print(f"  would fix: {r.sku} (status={r.status})")
        return

    with Session(engine) as session:
        stmt = select(ItemRecord).where(
            ItemRecord.item_mode == "review",
            ItemRecord.lot_group_id == None,  # noqa: E711
        )
        records = list(session.exec(stmt).all())
        for record in records:
            record.item_mode = "single"
            record.updated_at = datetime.utcnow()
            session.add(record)
        session.commit()

    print(f"Fixed {len(records)} item(s): item_mode set to 'single'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix incorrect item_mode='review' values")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database",
    )
    args = parser.parse_args()
    fix_item_modes(dry_run=args.dry_run)
