"""
MasterSheet — generates and updates the master inventory spreadsheet.
Mirrors every item in SQLite into a human-readable Excel file.
The spreadsheet is always derived from the database — never the other way around.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from packages.core.src.config import get_settings
from packages.domain.src.entities.item import Item


# Column definitions: (header, item_field, width)
COLUMNS = [
    ("Internal ID",       "internal_id",      24),
    ("SKU",               "sku",              14),
    ("Status",            "status",           18),
    ("Mode",              "item_mode",        10),
    ("Category",          "category_label",   14),
    ("Brand",             "brand",            16),
    ("Title",             "title_final",      44),
    ("Type",              "type",             14),
    ("Department",        "department",       12),
    ("Size",              "size",             10),
    ("Color",             "color",            12),
    ("Material",          "material",         16),
    ("Condition",         "condition_label",  22),
    ("Condition Notes",   "condition_notes",  28),
    ("Defects",           "defects",          28),
    ("Est. Price",        "estimated_price",  12),
    ("List Price",        "list_price",       12),
    ("Min Price",         "minimum_price",    12),
    ("Cost",              "cost",             10),
    ("Storage",           "storage_location", 14),
    ("Folder",            "photo_folder",     30),
    ("Image Count",       None,               12),
    ("Confidence",        "confidence_score", 12),
    ("Needs Review",      "needs_review",     14),
    ("Review Reasons",    "review_reasons",   30),
    ("Platform",          "platform",         10),
    ("Listing ID",        "listing_id",       20),
    ("Date Listed",       "date_listed",      16),
    ("Date Sold",         "date_sold",        16),
    ("Sold Price",        "sold_price",       12),
    ("Net Profit",        "net_profit",       12),
    ("Profit Margin",     "profit_margin",    14),
    ("Notes",             "notes",            30),
    ("Created",           "created_at",       18),
    ("Updated",           "updated_at",       18),
]

HEADER_FILL  = PatternFill("solid", fgColor="2C2C2A")
HEADER_FONT  = Font(color="F1EFE8", bold=True, size=11)
ALT_FILL     = PatternFill("solid", fgColor="F1EFE8")


class MasterSheetWriter:
    def __init__(self):
        self.settings = get_settings()

    def write(self, items: list[Item], output_path: Path | None = None) -> Path:
        if output_path is None:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = self.settings.export_dir / f"master_inventory_{ts}.xlsx"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "MASTER_INVENTORY"

        # Header row
        for col_idx, (header, _, width) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        ws.row_dimensions[1].height = 20
        ws.freeze_panes = "A2"

        # Data rows
        for row_idx, item in enumerate(items, start=2):
            fill = ALT_FILL if row_idx % 2 == 0 else None
            item_dict = item.model_dump()

            for col_idx, (_, field_name, _) in enumerate(COLUMNS, start=1):
                if field_name is None:
                    # Image Count
                    value = len(item.image_paths)
                else:
                    value = item_dict.get(field_name)

                # Format lists
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                elif isinstance(value, float) and field_name in ("profit_margin",):
                    value = f"{value:.1%}" if value else ""
                elif isinstance(value, float):
                    value = round(value, 2) if value else None
                elif isinstance(value, datetime):
                    value = value.strftime("%Y-%m-%d %H:%M")

                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                if fill:
                    cell.fill = fill

        # Auto-filter
        ws.auto_filter.ref = ws.dimensions

        wb.save(output_path)
        return output_path
