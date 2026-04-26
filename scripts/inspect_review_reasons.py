from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from packages.core.src.config import get_settings
from packages.testing.src.e2e_guard import assert_e2e_sku_allowed, get_approved_e2e_skus


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(get_settings().db_path)


def _backup_db() -> Path:
    settings = get_settings()
    out_dir = Path(settings.db_path).parent / "e2e_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"app_backup_review_reason_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(settings.db_path, out_path)
    return out_path


def _parse_skus(raw: str) -> list[str]:
    if not raw.strip():
        return sorted(get_approved_e2e_skus())
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def inspect(skus: list[str]) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        out = []
        for sku in skus:
            row = conn.execute(
                "SELECT sku, status, needs_review, review_reasons FROM items WHERE sku = ?",
                (sku,),
            ).fetchone()
            if row:
                out.append(dict(row))
        return out
    finally:
        conn.close()


def clear_stale_reason(skus: list[str], stale_reason: str) -> int:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    updated = 0
    try:
        for sku in skus:
            row = conn.execute(
                "SELECT review_reasons FROM items WHERE sku = ?",
                (sku,),
            ).fetchone()
            if not row:
                continue
            review_reasons = (row["review_reasons"] or "").strip()
            if not review_reasons:
                continue
            # Stored as JSON string list.
            import json

            try:
                parsed = json.loads(review_reasons)
            except Exception:
                parsed = []
            if not isinstance(parsed, list):
                continue
            new_reasons = [r for r in parsed if str(r) != stale_reason]
            if new_reasons != parsed:
                conn.execute(
                    "UPDATE items SET review_reasons = ?, updated_at = ? WHERE sku = ?",
                    (json.dumps(new_reasons), datetime.utcnow().isoformat(), sku),
                )
                updated += 1
        if updated:
            conn.commit()
    finally:
        conn.close()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect/optionally clear stale review reasons for approved E2E SKUs.")
    parser.add_argument("--skus", default="", help="Comma-separated SKU list. Defaults to approved E2E SKUs.")
    parser.add_argument("--clear-stale-reason", default="", help="Optional stale reason to remove.")
    args = parser.parse_args()

    skus = _parse_skus(args.skus)
    for sku in skus:
        assert_e2e_sku_allowed(sku)

    before = inspect(skus)
    print("Before:")
    for row in before:
        print(row)

    if args.clear_stale_reason:
        backup_path = _backup_db()
        print(f"Backup created: {backup_path}")
        updated = clear_stale_reason(skus, args.clear_stale_reason)
        print(f"Updated rows: {updated}")
        after = inspect(skus)
        print("After:")
        for row in after:
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
