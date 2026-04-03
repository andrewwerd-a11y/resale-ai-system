"""
Restore the database from a JSON backup.

Usage:
    uv run python scripts/restore_db.py --file data/exports/backup_20260401.json

WARNING: This overwrites existing data. Take a fresh backup first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def restore(backup_file: Path) -> None:
    if not backup_file.exists():
        print(f"ERROR: File not found: {backup_file}")
        sys.exit(1)

    with open(backup_file, encoding="utf-8") as f:
        payload = json.load(f)

    meta = payload.get("_meta", {})
    print(f"Restoring backup created at: {meta.get('created_at', 'unknown')}")
    print(f"  Items:        {len(payload.get('items', []))}")
    print(f"  SKU records:  {len(payload.get('sku_registry', []))}")
    print(f"  Sale records: {len(payload.get('sale_records', []))}")
    print()

    answer = input("This will overwrite the current database. Continue? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        sys.exit(0)

    from packages.data.src.db.sqlite import engine, init_db
    from packages.data.src.models.item_record import ItemRecord
    from packages.data.src.models.sku_record import SKURecord
    from packages.data.src.models.sale_record import SaleRecord
    from sqlmodel import Session, SQLModel, delete

    init_db()

    with Session(engine) as session:
        # Clear existing data
        session.exec(delete(ItemRecord))
        session.exec(delete(SKURecord))
        try:
            session.exec(delete(SaleRecord))
        except Exception:
            pass

        # Restore items
        for row in payload.get("items", []):
            try:
                record = ItemRecord(**{k: v for k, v in row.items() if hasattr(ItemRecord, k)})
                session.add(record)
            except Exception as e:
                print(f"  Warning: skipped item {row.get('sku')}: {e}")

        # Restore SKU registry
        for row in payload.get("sku_registry", []):
            try:
                record = SKURecord(**{k: v for k, v in row.items() if hasattr(SKURecord, k)})
                session.add(record)
            except Exception as e:
                print(f"  Warning: skipped SKU record {row.get('prefix')}: {e}")

        # Restore sale records
        for row in payload.get("sale_records", []):
            try:
                record = SaleRecord(**{k: v for k, v in row.items() if hasattr(SaleRecord, k)})
                session.add(record)
            except Exception as e:
                print(f"  Warning: skipped sale record: {e}")

        session.commit()

    print("Restore complete.")


if __name__ == "__main__":
    file_path = None
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--file" and i + 1 < len(args):
            file_path = Path(args[i + 1])
        elif not arg.startswith("--"):
            file_path = Path(arg)

    if not file_path:
        print("Usage: uv run python scripts/restore_db.py --file <backup.json>")
        sys.exit(1)

    restore(file_path)
