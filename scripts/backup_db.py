"""
Export the entire database to JSON for backup or machine transfer.

Usage:
    uv run python scripts/backup_db.py
    uv run python scripts/backup_db.py --output path/to/backup.json

Output: data/exports/backup_YYYYMMDD_HHMMSS.json (default)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def backup(output_path: Path | None = None) -> Path:
    from packages.core.src.config import get_settings
    from packages.data.src.db.sqlite import engine
    from packages.data.src.models.item_record import ItemRecord
    from packages.data.src.models.sku_record import SKURecord
    from packages.data.src.models.sale_record import SaleRecord
    from sqlmodel import Session, select

    settings = get_settings()
    settings.export_dir.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = settings.export_dir / f"backup_{ts}.json"

    payload: dict = {
        "_meta": {
            "created_at": datetime.utcnow().isoformat(),
            "version": "0.6.0",
        },
        "items": [],
        "sku_registry": [],
        "sale_records": [],
    }

    with Session(engine) as session:
        items = session.exec(select(ItemRecord)).all()
        payload["items"] = [r.model_dump(mode="json") for r in items]

        skus = session.exec(select(SKURecord)).all()
        payload["sku_registry"] = [r.model_dump(mode="json") for r in skus]

        try:
            sales = session.exec(select(SaleRecord)).all()
            payload["sale_records"] = [r.model_dump(mode="json") for r in sales]
        except Exception:
            pass  # table may not exist on older installs

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    item_count = len(payload["items"])
    print(f"Backup complete — {item_count} items → {output_path}")
    return output_path


if __name__ == "__main__":
    out = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            out = Path(arg)
    backup(out)
