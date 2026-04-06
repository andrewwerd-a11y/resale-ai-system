"""
EbayCSVWriter — converts approved Item records into an eBay Seller Hub
bulk upload CSV.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from packages.core.src.config import get_ebay_fields, get_settings
from packages.domain.src.entities.item import Item


# Fields that only apply to clothing/shoes — strip from other categories
CLOTHING_ONLY_FIELDS = {"Size", "Department", "Material", "Style", "Features"}
CLOTHING_CATEGORIES = {"clothing", "shoes"}


class EbayCSVWriter:
    def __init__(self):
        self.ebay_config = get_ebay_fields()
        self.settings = get_settings()
        self.columns = self.ebay_config["bulk_upload_columns"]
        self.field_map = self.ebay_config["field_map"]
        self.defaults = self.ebay_config["defaults"]

    def write(self, items: list[Item], output_path: Path | None = None) -> Path:
        if output_path is None:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = self.settings.export_dir / f"ebay_upload_{ts}.csv"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.columns, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                # Skip individual member items that belong to a lot group —
                # their lot header row covers them.
                if item.status == "lot_member" or (
                    item.lot_group_id
                    and item.item_mode == "lot"
                    and item.sku != item.lot_group_id
                ):
                    continue
                row = self._build_row(item)
                writer.writerow(row)

        return output_path

    def _build_row(self, item: Item) -> dict:
        row = dict(self.defaults)
        is_clothing = (item.category_key or "") in CLOTHING_CATEGORIES

        item_dict = item.model_dump()
        for internal_field, ebay_column in self.field_map.items():
            # Skip clothing-only fields for non-clothing items
            if ebay_column in CLOTHING_ONLY_FIELDS and not is_clothing:
                continue
            value = item_dict.get(internal_field)
            if value is not None and value != "" and value != []:
                if isinstance(value, list):
                    row[ebay_column] = ", ".join(str(v) for v in value)
                else:
                    row[ebay_column] = str(value)

        # Image paths — ensure we get actual file paths, not just folder paths
        image_paths = item.image_paths or []
        if isinstance(image_paths, str):
            image_paths = [p for p in image_paths.split("|") if p.strip()]

        img_count = 0
        for path_str in image_paths[:6]:
            path = Path(path_str)
            # If path is a directory, skip it
            if path.is_dir():
                continue
            # Only include if file actually exists
            if path.exists() and path.is_file():
                img_count += 1
                col = f"Photo URL {img_count}"
                if col in self.columns:
                    row[col] = str(path)

        # Required eBay fields
        if not row.get("Custom label (SKU)"):
            row["Custom label (SKU)"] = item.sku or ""
        if not row.get("Title"):
            row["Title"] = (item.title_final or item.title_raw or "")[:80]
        if not row.get("Price"):
            row["Price"] = str(item.list_price or item.estimated_price or 0)

        # Clean up title — remove "for Resale on eBay" type suffixes the model adds
        title = row.get("Title", "")
        for suffix in [" for Resale on eBay", " for Sale on eBay", " - Optimal eBay Listing Title"]:
            title = title.replace(suffix, "")
        row["Title"] = title[:80].strip()

        return row

    def preview(self, items: list[Item]) -> list[dict]:
        return [self._build_row(item) for item in items]
