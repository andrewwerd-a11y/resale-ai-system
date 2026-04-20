"""
CategorySpreadsheet — persistent, malleable category intelligence store.

Stored at data/category_intelligence/
One file per category: {category_id}_template.json
One summary file:      category_summary.csv

Malleable: can be edited manually or updated by the system.
Never deleted on republish — only appended/updated.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from packages.core.src.config import get_settings
from packages.domain.src.entities.item import Item
from packages.ebay.src.category_intelligence import CategoryTemplate

logger = logging.getLogger(__name__)


class CategorySpreadsheet:
    def __init__(self) -> None:
        settings = get_settings()
        self._dir = settings.db_path.parent / "category_intelligence"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._summary_path = self._dir / "category_summary.csv"

    # ── Public API ─────────────────────────────────────────────────────────────

    def save_template(self, template: CategoryTemplate) -> None:
        """Save/update template for a category."""
        path = self._template_path(template.category_id)
        data = {
            "category_id": template.category_id,
            "category_name": template.category_name,
            "required_fields": template.required_fields,
            "recommended_fields": template.recommended_fields,
            "field_constraints": template.field_constraints,
            "fetched_at": template.fetched_at.isoformat(),
            "raw_response": template.raw_response,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.debug("Saved category template %s → %s", template.category_id, path.name)
        self._update_summary_row(template)

    def load_template(self, category_id: str) -> CategoryTemplate | None:
        """Load cached template. Returns None if not found."""
        path = self._template_path(category_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CategoryTemplate(
                category_id=data["category_id"],
                category_name=data.get("category_name", f"Category {category_id}"),
                required_fields=data.get("required_fields", []),
                recommended_fields=data.get("recommended_fields", []),
                field_constraints=data.get("field_constraints", {}),
                fetched_at=datetime.fromisoformat(data.get("fetched_at", datetime.utcnow().isoformat())),
                raw_response=data.get("raw_response", {}),
            )
        except Exception as exc:
            logger.warning("Failed to load template for %s: %s", category_id, exc)
            return None

    def update_field_stats(
        self,
        category_id: str,
        item: Item,
        sold: bool = False,
        sold_price: float | None = None,
    ) -> None:
        """
        Update field fill rates and sales stats for a category.
        Called after publish and after sold sync.
        """
        summary = self._load_summary()
        row = summary.get(category_id, {
            "category_id": category_id,
            "category_name": "",
            "item_count": 0,
            "sold_count": 0,
            "total_revenue": 0.0,
            "avg_sold_price": 0.0,
            "required_fill_rate": 0.0,
            "last_updated": "",
        })

        row["item_count"] = int(row.get("item_count", 0)) + 1
        if sold:
            row["sold_count"] = int(row.get("sold_count", 0)) + 1
            if sold_price:
                total = float(row.get("total_revenue", 0)) + sold_price
                row["total_revenue"] = round(total, 2)
                sold_n = int(row.get("sold_count", 1))
                row["avg_sold_price"] = round(total / sold_n, 2)

        row["last_updated"] = datetime.utcnow().isoformat()
        summary[category_id] = row
        self._save_summary(summary)

    def get_summary(self) -> list[dict]:
        """Return summary of all categories with stats."""
        return list(self._load_summary().values())

    def export_csv(self, output_path: Path) -> None:
        """Export full category intelligence to CSV."""
        rows = self.get_summary()
        if not rows:
            output_path.write_text("No data", encoding="utf-8")
            return
        fieldnames = list(rows[0].keys())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info("Exported category intelligence to %s", output_path)

    # ── Internal ────────────────────────────────────────────────────────────────

    def _template_path(self, category_id: str) -> Path:
        return self._dir / f"{category_id}_template.json"

    def _load_summary(self) -> dict[str, dict]:
        if not self._summary_path.exists():
            return {}
        try:
            rows: dict[str, dict] = {}
            with self._summary_path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    cid = row.get("category_id", "")
                    if cid:
                        rows[cid] = dict(row)
            return rows
        except Exception as exc:
            logger.warning("Failed to load category summary: %s", exc)
            return {}

    def _save_summary(self, summary: dict[str, dict]) -> None:
        if not summary:
            return
        fieldnames = [
            "category_id", "category_name", "item_count", "sold_count",
            "total_revenue", "avg_sold_price", "required_fill_rate", "last_updated",
        ]
        with self._summary_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in summary.values():
                writer.writerow({k: row.get(k, "") for k in fieldnames})

    def _update_summary_row(self, template: CategoryTemplate) -> None:
        summary = self._load_summary()
        row = summary.get(template.category_id, {
            "category_id": template.category_id,
            "item_count": 0,
            "sold_count": 0,
            "total_revenue": 0.0,
            "avg_sold_price": 0.0,
            "required_fill_rate": 0.0,
        })
        row["category_name"] = template.category_name
        row["last_updated"] = datetime.utcnow().isoformat()
        summary[template.category_id] = row
        self._save_summary(summary)
