from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class E2EStep:
    name: str
    workflow: str
    status: str
    endpoint_or_function: str
    started_at: str
    ended_at: str
    duration_seconds: float
    sku: str | None = None
    request_summary: Any = None
    response_summary: Any = None
    error: str | None = None
    traceback: str | None = None
    notes: str | None = None


@dataclass
class E2EResult:
    mode: str
    base_url: str
    report_generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    branch: str | None = None
    commit: str | None = None
    db_backup_path: str | None = None
    approved_skus: list[str] = field(default_factory=list)
    environment_summary: dict[str, Any] = field(default_factory=dict)
    endpoint_coverage: list[dict[str, Any]] = field(default_factory=list)
    workflow_coverage: list[dict[str, Any]] = field(default_factory=list)
    steps: list[E2EStep] = field(default_factory=list)
    db_before: dict[str, Any] = field(default_factory=dict)
    db_after: dict[str, Any] = field(default_factory=dict)
    generated_files: list[str] = field(default_factory=list)
    failure_matrix: list[dict[str, Any]] = field(default_factory=list)
    restoration_log: list[dict[str, Any]] = field(default_factory=list)
    missing_cloudinary_upload_source: str = ""
    missing_cloudinary_upload_hypothesis: str = ""
    recommendations: list[str] = field(default_factory=list)
    safety_verdict: str = "unknown"


def _to_md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_None_\n"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = []
    for row in rows:
        vals = [str(row.get(c, "")) for c in columns]
        body.append("| " + " | ".join(vals) + " |")
    return "\n".join([header, sep, *body]) + "\n"


def render_markdown(result: E2EResult) -> str:
    endpoint_columns = ["name", "status", "status_code", "notes"]
    workflow_columns = ["workflow", "status", "notes"]
    step_rows = []
    for s in result.steps:
        step_rows.append(
            {
                "workflow": s.workflow,
                "name": s.name,
                "status": s.status,
                "sku": s.sku or "",
                "endpoint_or_function": s.endpoint_or_function,
                "duration_seconds": f"{s.duration_seconds:.3f}",
                "error": s.error or "",
            }
        )
    step_columns = [
        "workflow",
        "name",
        "status",
        "sku",
        "endpoint_or_function",
        "duration_seconds",
        "error",
    ]

    lines: list[str] = []
    lines.append("# Resale AI E2E Report")
    lines.append("")
    lines.append(f"- Timestamp: `{result.report_generated_at}`")
    lines.append(f"- Mode: `{result.mode}`")
    lines.append(f"- Base URL: `{result.base_url}`")
    lines.append(f"- Branch: `{result.branch or 'unknown'}`")
    lines.append(f"- Commit: `{result.commit or 'unknown'}`")
    lines.append(f"- DB backup: `{result.db_backup_path or 'none'}`")
    lines.append(f"- Approved SKUs: `{', '.join(result.approved_skus)}`")
    lines.append("")
    lines.append("## Environment Summary")
    lines.append("")
    lines.append("```json")
    lines.append(str(result.environment_summary))
    lines.append("```")
    lines.append("")
    lines.append("## Endpoint Coverage")
    lines.append("")
    lines.append(_to_md_table(result.endpoint_coverage, endpoint_columns))
    lines.append("## Workflow Coverage")
    lines.append("")
    lines.append(_to_md_table(result.workflow_coverage, workflow_columns))
    lines.append("## Step Results")
    lines.append("")
    lines.append(_to_md_table(step_rows, step_columns))
    lines.append("## DB State Before")
    lines.append("")
    lines.append("```json")
    lines.append(str(result.db_before))
    lines.append("```")
    lines.append("")
    lines.append("## DB State After")
    lines.append("")
    lines.append("```json")
    lines.append(str(result.db_after))
    lines.append("```")
    lines.append("")
    lines.append("## Generated Files")
    lines.append("")
    if result.generated_files:
        for path in result.generated_files:
            lines.append(f"- `{path}`")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Failure Matrix")
    lines.append("")
    lines.append(
        _to_md_table(
            result.failure_matrix,
            [
                "workflow",
                "simulated_failure",
                "expected_behavior",
                "actual_behavior",
                "item_state_changed",
                "unrelated_state_changed",
                "error_clear",
                "secrets_redacted",
                "file_function",
                "recommended_fix",
            ],
        )
    )
    lines.append("## Restoration Log")
    lines.append("")
    if result.restoration_log:
        for row in result.restoration_log:
            lines.append(
                f"- `{row.get('sku','')}`: restored={row.get('restored_fields', [])}, "
                f"skipped={row.get('skipped_fields', [])}, note={row.get('note','')}"
            )
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## missing_cloudinary_upload Investigation")
    lines.append("")
    lines.append(f"- Source: {result.missing_cloudinary_upload_source or 'unknown'}")
    lines.append(
        f"- Root cause hypothesis: {result.missing_cloudinary_upload_hypothesis or 'unknown'}"
    )
    lines.append("")
    lines.append("## Recommendations")
    lines.append("")
    if result.recommendations:
        for i, rec in enumerate(result.recommendations, start=1):
            lines.append(f"{i}. {rec}")
    else:
        lines.append("1. No recommendations recorded.")
    lines.append("")
    lines.append("## Safety Verdict")
    lines.append("")
    lines.append(f"`{result.safety_verdict}`")
    lines.append("")
    return "\n".join(lines)


def write_markdown(result: E2EResult, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(result), encoding="utf-8")
    return output_path

