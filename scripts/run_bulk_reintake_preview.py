from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlmodel import Session

from apps.api.src.services.bulk_reintake_preview import (
    DEFAULT_BULK_REINTAKE_STATUSES,
    DEFAULT_REPORT_DIR,
    build_bulk_reintake_preview,
)
from packages.data.src.db.sqlite import engine


def _csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only bulk reintake preview report. Never publishes or mutates item records."
    )
    parser.add_argument("--skus", default="", help="Comma-separated SKUs. Defaults to status-based selection.")
    parser.add_argument(
        "--statuses",
        default=",".join(DEFAULT_BULK_REINTAKE_STATUSES),
        help="Comma-separated local statuses to include when --skus is omitted.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Generated local report directory. Do not commit these artifacts.",
    )
    parser.add_argument(
        "--run-deep-analysis-preview",
        action="store_true",
        help="Opt into deep-analysis preview. Disabled by default to avoid provider calls.",
    )
    parser.add_argument(
        "--allow-live-readonly",
        action="store_true",
        help="Opt into live read-only eBay diagnostics. Never performs eBay mutation.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the preview summary without writing JSON/Markdown reports.",
    )
    args = parser.parse_args()

    with Session(engine) as session:
        preview = build_bulk_reintake_preview(
            session,
            skus=_csv(args.skus),
            statuses=_csv(args.statuses),
            run_deep_analysis_preview=args.run_deep_analysis_preview,
            allow_live_readonly=args.allow_live_readonly,
            report_dir=Path(args.report_dir),
            write_reports=not args.no_write,
        )

    print(json.dumps({
        "report_id": preview["report_id"],
        "summary": preview["summary"],
        "json_report_path": preview.get("json_report_path"),
        "markdown_report_path": preview.get("markdown_report_path"),
        "safety": {
            "read_only": preview["read_only"],
            "draft_only": preview["draft_only"],
            "no_publish_performed": preview["no_publish_performed"],
            "no_ebay_mutation_performed": preview["no_ebay_mutation_performed"],
            "no_external_provider_called": preview["no_external_provider_called"],
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
