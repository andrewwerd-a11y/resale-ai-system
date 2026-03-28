from __future__ import annotations
import csv
from datetime import datetime
from pathlib import Path

from packages.domain.src.entities.item import Item
from packages.core.src.config import get_settings
from packages.classification.src.category_mapper import get_ebay_category_id


EBAY_CSV_COLUMNS = [
    "Action", "ItemID", "Category", "Title", "Description",
    "StartPrice", "BuyItNowPrice", "Quantity", "Duration",
    "Condition", "ConditionID", "PicURL",
    "DispatchTimeMax", "ShippingType", "ShippingService-1:Option",
    "ShippingService-1:Cost", "CustomLabel",
]


def _build_description(item: Item) -> str:
    parts = []
    if item.title:
        parts.append(item.title)
    if item.brand:
        parts.append(f"Brand: {item.brand}")
    if item.condition:
        parts.append(f"Condition: {item.condition}")
    if item.condition_notes:
        parts.append(f"Notes: {item.condition_notes}")
    if item.features:
        parts.append("Features: " + ", ".join(item.features))
    if item.defects:
        parts.append("Defects: " + ", ".join(item.defects))
    return "\n".join(parts)


def item_to_ebay_row(item: Item) -> dict:
    category_id = get_ebay_category_id(item.category or "Collectibles")
    pic_url = ""
    if item.hosted_photo_urls:
        pic_url = "|".join(item.hosted_photo_urls[:12])
    elif item.image_paths:
        pic_url = ""  # Local paths not usable in CSV

    return {
        "Action": "Add",
        "ItemID": "",
        "Category": category_id,
        "Title": (item.title or "")[:80],
        "Description": _build_description(item),
        "StartPrice": item.list_price or item.estimated_price or 0,
        "BuyItNowPrice": "",
        "Quantity": 1,
        "Duration": "GTC",
        "Condition": item.condition or "Good",
        "ConditionID": item.condition_id or "3000",
        "PicURL": pic_url,
        "DispatchTimeMax": 3,
        "ShippingType": "Flat",
        "ShippingService-1:Option": "USPSFirstClass",
        "ShippingService-1:Cost": 4.99,
        "CustomLabel": item.sku,
    }


def generate_ebay_csv(items: list[Item], output_path: Path | None = None) -> Path:
    settings = get_settings()
    if output_path is None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = settings.export_dir / f"ebay_bulk_{ts}.csv"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EBAY_CSV_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(item_to_ebay_row(item))

    return output_path
