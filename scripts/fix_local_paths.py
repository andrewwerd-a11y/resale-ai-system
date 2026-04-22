"""
scripts/fix_local_paths.py

One-time repair: finds items where image_paths contains Windows local file paths
instead of Cloudinary URLs. Moves them to needs_review / photo_blocked so they
can be re-uploaded and re-published.

DRY RUN by default. Pass --apply to write changes.

Usage:
    uv run python scripts/fix_local_paths.py          # dry run — print plan only
    uv run python scripts/fix_local_paths.py --apply  # write changes to DB
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from sqlmodel import Session, select

from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord


def _has_local_path(image_paths: str | None) -> bool:
    if not image_paths:
        return False
    for p in image_paths.split("|"):
        p = p.strip()
        if p.startswith("C:\\") or p.startswith("C:/") or (len(p) > 1 and p[1] == ":"):
            return True
    return False


def main(apply: bool = False) -> None:
    with Session(engine) as session:
        records = session.exec(select(ItemRecord)).all()

        affected = [r for r in records if _has_local_path(r.image_paths)]

        if not affected:
            print("No items with local file paths found. Nothing to do.")
            return

        print(f"\nFound {len(affected)} item(s) with local file paths:\n")
        print(f"  {'SKU':<14} {'Status':<18} {'Paths (truncated)'}")
        print(f"  {'-'*13} {'-'*17} {'-'*50}")
        for rec in affected:
            paths_preview = (rec.image_paths or "")[:80]
            print(f"  {rec.sku:<14} {rec.status:<18} {paths_preview}")

        print()
        if not apply:
            print("DRY RUN — no changes written.")
            print("Run with --apply to set each item to needs_review / photo_blocked.\n")
            print("Planned changes per item:")
            for rec in affected:
                print(f"  {rec.sku}: status → needs_review, review_sub_queue → photo_blocked, concern_flag → missing_cloudinary_upload")
            return

        # Apply changes
        import json
        updated = []
        for rec in affected:
            # Preserve existing concern_flags
            existing_flags: list = []
            if rec.concern_flags:
                try:
                    existing_flags = json.loads(rec.concern_flags) if isinstance(rec.concern_flags, str) else list(rec.concern_flags)
                except Exception:
                    existing_flags = []
            if "missing_cloudinary_upload" not in existing_flags:
                existing_flags.append("missing_cloudinary_upload")

            # Only move back to review if currently exported/listed (not already review/rejected)
            if rec.status in ("exported", "listed"):
                rec.status = "needs_review"

            rec.needs_review = True
            rec.review_sub_queue = "photo_blocked"
            rec.concern_flags = json.dumps(existing_flags)

            # Add review reason if not present
            existing_reasons: list = []
            if rec.review_reasons:
                try:
                    existing_reasons = json.loads(rec.review_reasons) if isinstance(rec.review_reasons, str) else list(rec.review_reasons)
                except Exception:
                    existing_reasons = []
            if "missing_cloudinary_upload" not in existing_reasons:
                existing_reasons.append("missing_cloudinary_upload")
            rec.review_reasons = json.dumps(existing_reasons)

            session.add(rec)
            updated.append(rec.sku)

        session.commit()
        print(f"Updated {len(updated)} item(s): {', '.join(updated)}")
        print("These items are now in needs_review / photo_blocked.")
        print("Re-upload photos via the UI or scripts/publish_all.py after fixing paths.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix items with local file paths in image_paths")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default is dry run)")
    args = parser.parse_args()
    main(apply=args.apply)
