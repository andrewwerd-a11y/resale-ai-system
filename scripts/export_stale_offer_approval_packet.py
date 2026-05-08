from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.src.services.publish_diagnostics import build_publish_diagnostics
from apps.api.src.services.stale_offer_remediation import (
    build_stale_offer_remediation_approval_preview,
    render_stale_offer_remediation_approval_packet,
)
from packages.data.src.db.sqlite import engine
from packages.data.src.repositories.item_repo import ItemRepository


def export_packet(*, sku: str, output: Path | None = None, allow_live_readonly: bool = False) -> Path:
    normalized = str(sku or "").strip().upper()
    if not normalized:
        raise ValueError("--sku is required")

    with Session(engine) as session:
        diagnostics = build_publish_diagnostics(
            session,
            normalized,
            allow_live_readonly=allow_live_readonly,
        )
        if not diagnostics.get("found"):
            raise ValueError(f"SKU {normalized} was not found")
        item = ItemRepository(session).get_by_sku(normalized)
        preview = build_stale_offer_remediation_approval_preview(diagnostics)
        if item:
            preview["local_item_summary"]["title"] = item.title_final or item.title_raw or ""

    report_path = output or Path("data") / "remediation_reports" / f"{normalized}_approval_packet.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        render_stale_offer_remediation_approval_packet(preview),
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a read-only stale-offer remediation approval packet.")
    parser.add_argument("--sku", required=True, help="Explicit SKU to inspect, e.g. BK-000008.")
    parser.add_argument("--output", default="", help="Optional Markdown output path.")
    parser.add_argument(
        "--allow-live-readonly",
        action="store_true",
        help="Allow existing GET-only eBay diagnostics. No eBay mutation is performed.",
    )
    args = parser.parse_args()
    path = export_packet(
        sku=args.sku,
        output=Path(args.output) if args.output else None,
        allow_live_readonly=args.allow_live_readonly,
    )
    print(f"Approval packet written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
