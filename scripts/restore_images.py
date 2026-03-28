"""
Re-scan source folders and restore image_paths for items in DB.
Use this after reset_pending.py if image paths were cleared.

Usage:
  uv run python scripts/restore_images.py --source "C:\\Users\\Andrew\\Desktop\\reselling_system_template\\Inventory_Photos"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository
from packages.intake.src.folder_scanner import scan_item_folders
from packages.intake.src.image_normalizer import normalize_paths
from packages.sku.src.registry import extract_sku_from_folder


def main():
    parser = argparse.ArgumentParser(description="Restore image paths from source directory")
    parser.add_argument("--source", required=True, help="Path to inventory photos folder")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"Error: source directory not found: {source}")
        sys.exit(1)

    init_db()
    folders = scan_item_folders(source)
    print(f"Found {len(folders)} folders")

    updated = 0
    not_found = 0

    for folder in folders:
        sku = extract_sku_from_folder(folder["folder_name"])
        if not sku:
            continue

        images = normalize_paths(folder["image_paths"])
        if not images:
            continue

        with get_session() as session:
            repo = ItemRepository(session)
            item = repo.get_by_sku(sku)
            if item is None:
                not_found += 1
                continue
            updated_item = item.model_copy(update={"image_paths": images})
            repo.upsert(updated_item)
            updated += 1
            print(f"  [OK] {sku} — {len(images)} images")

    print(f"\nUpdated: {updated}, Not in DB: {not_found}")


if __name__ == "__main__":
    main()
