"""
Sales reporting endpoints.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from apps.api.src.services.ebay_auth_diagnostics import get_ebay_auth_readiness
from packages.data.src.db.sqlite import get_session
from packages.data.src.models.sale_record import SaleRecord

router = APIRouter()


# ── Category Intelligence Reports ─────────────────────────────────────────────

@router.get("/category-intelligence")
def category_intelligence_report():
    """Return category spreadsheet summary — fill rates, avg prices, trends."""
    from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
    sheet = CategorySpreadsheet()
    return sheet.get_summary()


@router.get("/category-intelligence/export")
def export_category_intelligence():
    """Export full category intelligence CSV."""
    import tempfile
    from pathlib import Path
    from packages.ebay.src.category_spreadsheet import CategorySpreadsheet

    sheet = CategorySpreadsheet()
    rows = sheet.get_summary()
    if not rows:
        output = io.StringIO()
        output.write("No category intelligence data available.\n")
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=category_intelligence.csv"},
        )

    output = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    filename = f"category_intelligence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


@router.get("/sold-readiness")
def sold_readiness(session: Session = Depends(get_session)):
    records = session.exec(select(SaleRecord)).all()
    ebay_records = [record for record in records if (record.platform or "").lower() == "ebay"]
    ebay_sync_records = [record for record in ebay_records if str(record.source_report or "").startswith("ebay_order:")]
    last_sync_candidates = [record.created_at for record in ebay_sync_records if record.created_at]
    last_sync_at = max(last_sync_candidates).isoformat() if last_sync_candidates else None

    auth_readiness = get_ebay_auth_readiness()
    sold_sync_ready = bool(auth_readiness.get("checks", {}).get("access_token_present")) and not auth_readiness.get("blockers")
    warnings: list[str] = []

    if not records:
        warnings.append("No sold records yet.")
    if not sold_sync_ready:
        warnings.append("Sold sync is not ready because eBay auth needs attention.")

    return {
        "ready": sold_sync_ready,
        "total_sold_records": len(records),
        "ebay_sold_records": len(ebay_records),
        "last_sold_sync_at": last_sync_at,
        "duplicate_protection": {
            "enabled": True,
            "mode": "source_report_idempotency",
            "detail": "eBay sold sync deduplicates using order/line source_report keys when available.",
        },
        "unknown_sku_count": None,
        "unknown_sku_count_tracked": False,
        "sold_sync_auth": {
            "ready": sold_sync_ready,
            "code": auth_readiness.get("code"),
            "message": auth_readiness.get("message"),
            "next_action": auth_readiness.get("next_action"),
            "environment": auth_readiness.get("checks", {}).get("environment"),
        },
        "warnings": warnings,
    }


@router.get("/sales")
def list_sales(
    platform: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 500,
    session: Session = Depends(get_session),
):
    records = session.exec(select(SaleRecord)).all()
    results = []
    dt_from = _parse_dt(date_from)
    dt_to = _parse_dt(date_to)
    for r in records:
        if platform and r.platform != platform:
            continue
        ds = r.date_sold
        if dt_from and ds and ds < dt_from:
            continue
        if dt_to and ds and ds > dt_to:
            continue
        results.append(r.model_dump())
    results.sort(key=lambda x: str(x.get("date_sold") or ""), reverse=True)
    return results[:limit]


@router.get("/summary")
def sales_summary(session: Session = Depends(get_session)):
    records = session.exec(select(SaleRecord)).all()
    if not records:
        return {
            "total_revenue": 0, "total_gross_profit": 0, "total_net_profit": 0,
            "avg_gross_margin": 0, "avg_net_margin": 0, "total_sales": 0,
        }
    total_revenue = sum(r.sold_price for r in records)
    total_gross = sum(r.gross_profit for r in records)
    total_net = sum(r.net_profit for r in records)
    n = len(records)
    return {
        "total_revenue": round(total_revenue, 2),
        "total_gross_profit": round(total_gross, 2),
        "total_net_profit": round(total_net, 2),
        "avg_gross_margin": round(sum(r.gross_margin for r in records) / n, 4),
        "avg_net_margin": round(sum(r.net_margin for r in records) / n, 4),
        "total_sales": n,
    }


@router.get("/by-category")
def sales_by_category(session: Session = Depends(get_session)):
    from packages.data.src.models.item_record import ItemRecord

    records = session.exec(select(SaleRecord)).all()
    items_map = {
        i.sku: (i.category_label or "Unknown")
        for i in session.exec(select(ItemRecord)).all()
        if i.sku
    }
    buckets: dict[str, dict] = {}
    for r in records:
        cat = items_map.get(r.sku, "Unknown")
        b = buckets.setdefault(cat, {"category": cat, "sales": 0, "revenue": 0.0, "net_profit": 0.0})
        b["sales"] += 1
        b["revenue"] += r.sold_price
        b["net_profit"] += r.net_profit
    for b in buckets.values():
        b["revenue"] = round(b["revenue"], 2)
        b["net_profit"] = round(b["net_profit"], 2)
    return sorted(buckets.values(), key=lambda x: x["revenue"], reverse=True)


@router.get("/by-platform")
def sales_by_platform(session: Session = Depends(get_session)):
    records = session.exec(select(SaleRecord)).all()
    buckets: dict[str, dict] = {}
    for r in records:
        b = buckets.setdefault(r.platform, {"platform": r.platform, "sales": 0, "revenue": 0.0, "net_profit": 0.0})
        b["sales"] += 1
        b["revenue"] += r.sold_price
        b["net_profit"] += r.net_profit
    for b in buckets.values():
        b["revenue"] = round(b["revenue"], 2)
        b["net_profit"] = round(b["net_profit"], 2)
    return sorted(buckets.values(), key=lambda x: x["revenue"], reverse=True)


@router.get("/by-month")
def sales_by_month(session: Session = Depends(get_session)):
    records = session.exec(select(SaleRecord)).all()
    buckets: dict[str, dict] = {}
    for r in records:
        if not r.date_sold:
            continue
        key = r.date_sold.strftime("%Y-%m") if hasattr(r.date_sold, "strftime") else str(r.date_sold)[:7]
        b = buckets.setdefault(key, {"month": key, "sales": 0, "revenue": 0.0, "net_profit": 0.0})
        b["sales"] += 1
        b["revenue"] += r.sold_price
        b["net_profit"] += r.net_profit
    for b in buckets.values():
        b["revenue"] = round(b["revenue"], 2)
        b["net_profit"] = round(b["net_profit"], 2)
    return sorted(buckets.values(), key=lambda x: x["month"])


@router.post("/export-csv")
def export_sales_csv(session: Session = Depends(get_session)):
    from packages.data.src.models.item_record import ItemRecord

    records = session.exec(select(SaleRecord)).all()
    items_map = {i.sku: i for i in session.exec(select(ItemRecord)).all() if i.sku}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "SKU", "Title", "Platform", "Sold Price", "Cost", "Fees",
        "Shipping", "Gross Profit", "Net Profit", "Net Margin %",
        "Date Sold", "Listing ID",
    ])
    for r in sorted(records, key=lambda x: x.date_sold or datetime.min, reverse=True):
        item = items_map.get(r.sku)
        title = (item.title_final or item.title_raw or "") if item else ""
        ds = r.date_sold.strftime("%Y-%m-%d") if hasattr(r.date_sold, "strftime") else str(r.date_sold)[:10]
        writer.writerow([
            r.sku, title, r.platform,
            f"{r.sold_price:.2f}",
            f"{r.cost:.2f}" if r.cost is not None else "",
            f"{r.fees:.2f}",
            f"{r.shipping_cost:.2f}",
            f"{r.gross_profit:.2f}",
            f"{r.net_profit:.2f}",
            f"{r.net_margin * 100:.1f}%",
            ds,
            r.listing_id or "",
        ])

    output.seek(0)
    filename = f"sales_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
