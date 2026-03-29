"""
Fix image_paths in DB: replace .jpeg extensions with .jpg.

If a .jpg file doesn't exist but a .jpeg does, also renames the file on disk.

Usage:
  uv run python scripts/fix_jpeg_paths.py
  uv run python scripts/fix_jpeg_paths.py --dry-run
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data.src.db.sqlite import init_db, get_session
from packages.data.src.repositories.item_repo import ItemRepository


def fix_paths(paths: list[str], rename_files: bool = True) -> tuple[list[str], bool]:
    """Return (fixed_paths, changed). Renames .jpeg → .jpg on disk if needed."""
    changed = False
    result = []
    for p in paths:
        path = Path(p)
        if path.suffix.lower() == ".jpeg":
            jpg_path = path.with_suffix(".jpg")
            if rename_files and path.exists() and not jpg_path.exists():
                try:
                    path.rename(jpg_path)
                    print(f"    Renamed: {path.name} → {jpg_path.name}")
                except Exception as e:
                    print(f"    Could not rename {path.name}: {e}")
            result.append(str(jpg_path))
            changed = True
        else:
            result.append(p)
    return result, changed


def main():
    parser = argparse.ArgumentParser(description="Fix .jpeg → .jpg in DB image_paths")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--no-rename", action="store_true", help="Fix DB only, don't rename files")
    args = parser.parse_args()

    init_db()

    with get_session() as session:
        repo = ItemRepository(session)
        items = repo.list_all()

    fixed_count = 0
    for item in items:
        new_paths, changed = fix_paths(item.image_paths, rename_files=not args.no_rename)
        if not changed:
            continue

        if args.dry_run:
            print(f"[DRY] {item.sku}: {item.image_paths} → {new_paths}")
            fixed_count += 1
            continue

        updated = item.model_copy(update={"image_paths": new_paths})
        with get_session() as session:
            repo = ItemRepository(session)
            repo.upsert(updated)
        print(f"[OK]  {item.sku}: fixed {len(new_paths)} paths")
        fixed_count += 1

    print(f"\nFixed {fixed_count} items.")


if __name__ == "__main__":
    main()
