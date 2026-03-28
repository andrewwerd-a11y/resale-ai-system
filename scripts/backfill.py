"""
Import existing item folders from a source directory into the database.

Usage:
  uv run python scripts/backfill.py --source "C:\\Users\\Andrew\\Desktop\\reselling_system_template\\Inventory_Photos"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.data.src.repositories.sku_repo import SKURepository
from packages.domain.src.entities.item import Item
from packages.intake.src.folder_scanner import scan_item_folders
from packages.intake.src.image_normalizer import normalize_paths
from packages.sku.src.registry import extract_sku_from_folder, guess_category_from_folder, category_to_prefix


def main():
    parser = argparse.ArgumentParser(description="Import inventory folders into DB")
    parser.add_argument("--source", required=True, help="Path to inventory photos folder")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"Error: source directory not found: {source}")
        sys.exit(1)

    init_db()

    folders = scan_item_folders(source)
    print(f"Found {len(folders)} item folders in {source}")

    imported = 0
    skipped = 0

    for folder in folders:
        folder_name = folder["folder_name"]
        image_paths = normalize_paths(folder["image_paths"])

        # Try to extract existing SKU from folder name
        existing_sku = extract_sku_from_folder(folder_name)
        category = guess_category_from_folder(folder_name)

        with get_session() as session:
            item_repo = ItemRepository(session)
            sku_repo = SKURepository(session)

            if existing_sku:
                sku = existing_sku
                # Register in SKU table if not present
                if not sku_repo.get(sku):
                    prefix = sku.split("-")[0]
                    seq = int(sku.split("-")[1])
                    from packages.data.src.models.sku_record import SKURecord
                    record = SKURecord(sku=sku, prefix=prefix, sequence=seq, category=category)
                    session.add(record)
                    session.commit()
            else:
                # Generate new SKU
                prefix = category_to_prefix(category)
                sku = sku_repo.create(prefix, category)

            # Check if already exists
            existing = item_repo.get_by_sku(sku)
            if existing:
                skipped += 1
                if args.dry_run:
                    print(f"  [SKIP] {sku} — already in DB")
                continue

            if args.dry_run:
                print(f"  [DRY]  {sku} — {len(image_paths)} photos — {category}")
                imported += 1
                continue

            item = Item(
                sku=sku,
                category=category,
                image_paths=image_paths,
                status="pending_intake",
            )
            item_repo.upsert(item)
            imported += 1
            print(f"  [OK]   {sku} — {len(image_paths)} photos — {category}")

    print(f"\nDone. Imported: {imported}, Skipped (already exists): {skipped}")


if __name__ == "__main__":
    main()
