from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from apps.api.src.services.operation_diagnostics import event_to_dict
from packages.core.src.config import get_settings
from packages.data.src.models.operation_diagnostic_event_record import OperationDiagnosticEventRecord

DIAGNOSTIC_REPORT_VERSION = "diagnostic-reporting.v1"
NO_EXTERNAL_SEND = True
REDACTION_NOTICE = "Sensitive fields are redacted and raw examples are bounded for local debugging use only."


@dataclass
class ReportFilters:
    session_id: str | None = None
    sku: str | None = None
    days: int | None = None
    severity: str | None = None


def generate_critical_error_report(session: Session) -> dict:
    events = _load_events(session, ReportFilters())
    repeated = _repeated_failures(events)
    enriched = _enrich_events(events, repeated)
    critical_events = [event for event in enriched if event["severity"] == "critical"]
    return _build_report(
        report_type="critical_error_report",
        title="Critical Error Report",
        events=critical_events,
        filters=ReportFilters(severity="critical"),
    )


def generate_session_report(session: Session, session_id: str) -> dict:
    events = _load_events(session, ReportFilters(session_id=session_id))
    repeated = _repeated_failures(events)
    return _build_report(
        report_type="session_report",
        title=f"Session Report - {session_id}",
        events=_enrich_events(events, repeated),
        filters=ReportFilters(session_id=session_id),
    )


def generate_sku_report(session: Session, sku: str) -> dict:
    normalized = (sku or "").strip().upper()
    events = _load_events(session, ReportFilters(sku=normalized))
    repeated = _repeated_failures(events)
    return _build_report(
        report_type="sku_report",
        title=f"SKU Report - {normalized}",
        events=_enrich_events(events, repeated),
        filters=ReportFilters(sku=normalized),
    )


def generate_weekly_report(session: Session, *, days: int = 7) -> dict:
    events = _load_events(session, ReportFilters(days=days))
    repeated = _repeated_failures(events)
    return _build_report(
        report_type="weekly_report",
        title=f"Weekly Error Report - Last {days} Days",
        events=_enrich_events(events, repeated),
        filters=ReportFilters(days=days),
    )


def generate_root_cause_analysis_package(
    session: Session,
    *,
    session_id: str | None = None,
    sku: str | None = None,
    days: int | None = None,
) -> dict:
    events = _load_events(session, ReportFilters(session_id=session_id, sku=sku, days=days))
    repeated = _repeated_failures(events)
    report = _build_report(
        report_type="root_cause_analysis_package",
        title="Root Cause Analysis Package",
        events=_enrich_events(events, repeated),
        filters=ReportFilters(session_id=session_id, sku=sku, days=days),
    )
    report["analysis_focus"] = [
        "Top repeated failures",
        "Severity escalation across routes and SKUs",
        "Suspected shared root causes",
        "Recommended next actions",
    ]
    return report


def generate_report(
    session: Session,
    *,
    report_type: str,
    session_id: str | None = None,
    sku: str | None = None,
    days: int | None = None,
    persist: bool = True,
) -> dict:
    if report_type == "critical_error_report":
        report = generate_critical_error_report(session)
    elif report_type == "session_report":
        report = generate_session_report(session, session_id or "")
    elif report_type == "sku_report":
        report = generate_sku_report(session, sku or "")
    elif report_type == "weekly_report":
        report = generate_weekly_report(session, days=days or 7)
    elif report_type == "root_cause_analysis_package":
        report = generate_root_cause_analysis_package(session, session_id=session_id, sku=sku, days=days)
    else:
        raise ValueError(f"Unsupported diagnostic report type: {report_type}")

    if persist:
        report["persisted_files"] = persist_report(report)
    else:
        report["persisted_files"] = []
    return report


def persist_report(report: dict) -> list[str]:
    reports_dir = _reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{timestamp}_{report['report_type']}_{report['report_id']}"
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(render_report_markdown(report), encoding="utf-8")
    return [str(json_path), str(md_path)]


def list_recent_reports(*, limit: int = 20) -> list[dict]:
    reports_dir = _reports_dir()
    if not reports_dir.exists():
        return []
    files = sorted(reports_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[: max(1, min(limit, 100))]
    reports = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        reports.append(
            {
                "report_id": payload.get("report_id") or path.stem,
                "report_type": payload.get("report_type") or "",
                "title": payload.get("title") or "",
                "generated_at": payload.get("generated_at") or "",
                "summary_counts": payload.get("summary_counts") or {},
                "severity_breakdown": payload.get("severity_breakdown") or {},
                "json_path": str(path),
                "markdown_path": str(path.with_suffix(".md")),
            }
        )
    return reports


def render_report_markdown(report: dict) -> str:
    lines = [
        f"# {report.get('title') or 'Diagnostic Report'}",
        "",
        f"- Report ID: `{report.get('report_id')}`",
        f"- Report Type: `{report.get('report_type')}`",
        f"- Generated At: `{report.get('generated_at')}`",
        f"- Diagnostic Version: `{report.get('diagnostic_version')}`",
        f"- No External Send: `{report.get('no_external_send')}`",
        "",
        "## Summary Counts",
    ]
    for key, value in (report.get("summary_counts") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Severity Breakdown"])
    for key, value in (report.get("severity_breakdown") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Top Error Families"])
    for entry in report.get("top_error_families") or []:
        lines.append(f"- {entry['error_family']}: {entry['count']}")
    lines.extend(["", "## Repeated Failures"])
    repeated = report.get("repeated_failures") or []
    if repeated:
        for entry in repeated:
            lines.append(
                f"- {entry['group_key']}: count={entry['count']}, sku_count={entry['sku_count']}, severity={entry['severity']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Recommended Next Actions"])
    for action in report.get("recommended_next_actions") or []:
        lines.append(f"- {action}")
    lines.extend(["", "## Affected SKUs"])
    affected_skus = report.get("affected_skus") or []
    lines.append(f"- {', '.join(affected_skus) if affected_skus else 'none'}")
    return "\n".join(lines)


def build_copyable_codex_prompt(report: dict) -> str:
    return (
        "Analyze this local diagnostic report for the Resale AI System. "
        "Focus on repeated failures, severity distribution, likely shared root causes, and the safest next actions. "
        "Do not recommend external reporting or live eBay mutation unless separately approved.\n\n"
        f"{render_report_markdown(report)}"
    )


def classify_event_severity(event: dict, *, repeated_across_skus: bool = False) -> str:
    status = str(event.get("status") or "").lower()
    route = str(event.get("route") or "").lower()
    external_service = str(event.get("external_service") or "").lower()
    safe_message = str(event.get("safe_message") or "").lower()
    error_family = str(event.get("error_family") or "").lower()
    mutation_attempted = bool(event.get("mutation_attempted"))
    mutation_succeeded = bool(event.get("mutation_succeeded"))
    ebay_mutation_attempted = bool(event.get("ebay_mutation_attempted"))
    ebay_mutation_succeeded = bool(event.get("ebay_mutation_succeeded"))

    if repeated_across_skus:
        return "critical"
    if external_service == "database" and status == "failed":
        return "critical"
    if external_service == "ebay" and ebay_mutation_attempted and not ebay_mutation_succeeded:
        return "critical"
    if str(event.get("operation_name") or "") == "photo_hosting" and mutation_attempted and not mutation_succeeded:
        return "critical"
    if ("auth" in error_family or "token" in safe_message) and status in {"failed", "blocked"}:
        return "critical"
    if error_family == "unexpected_exception" and any(part in route for part in ("publish", "revise", "sync")):
        return "critical"
    if status == "failed":
        return "high"
    if status == "blocked":
        return "medium"
    if status == "warning":
        return "low"
    return "info"


def _build_report(*, report_type: str, title: str, events: list[dict], filters: ReportFilters) -> dict:
    now = datetime.now(timezone.utc)
    repeated = _repeated_failures(events)
    severity_breakdown = Counter(event["severity"] for event in events)
    top_error_families = _top_counts(events, "error_family")
    top_routes = _top_counts(events, "route")
    top_operations = _top_counts(events, "operation_name")
    affected_skus = sorted({str(event.get("sku") or "") for event in events if str(event.get("sku") or "")})
    first_seen = min((event["created_at"] for event in events), default="")
    last_seen = max((event["created_at"] for event in events), default="")
    report = {
        "report_id": f"diag-{now.strftime('%Y%m%d%H%M%S')}",
        "report_type": report_type,
        "title": title,
        "generated_at": now.isoformat(),
        "diagnostic_version": DIAGNOSTIC_REPORT_VERSION,
        "no_external_send": NO_EXTERNAL_SEND,
        "redaction_notice": REDACTION_NOTICE,
        "filters": {
            "session_id": filters.session_id,
            "sku": filters.sku,
            "days": filters.days,
            "severity": filters.severity,
        },
        "git_context": _git_context(),
        "summary_counts": {
            "events_total": len(events),
            "failed": sum(1 for event in events if event.get("status") == "failed"),
            "blocked": sum(1 for event in events if event.get("status") == "blocked"),
            "warning": sum(1 for event in events if event.get("status") == "warning"),
            "success": sum(1 for event in events if event.get("status") == "success"),
        },
        "severity_breakdown": {key: severity_breakdown.get(key, 0) for key in ("critical", "high", "medium", "low", "info")},
        "top_error_families": top_error_families,
        "affected_skus": affected_skus,
        "affected_routes": top_routes,
        "affected_operations": top_operations,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "repeated_failures": repeated,
        "suspected_root_causes": _suspected_root_causes(events, repeated),
        "recommended_next_actions": _recommended_actions(events, repeated),
        "related_files_services": _related_files_services(events),
        "sanitized_raw_examples": _sanitized_raw_examples(events),
        "events_sample": events[:25],
    }
    report["copyable_codex_prompt"] = build_copyable_codex_prompt(report)
    report["report_markdown"] = render_report_markdown(report)
    return report


def _load_events(session: Session, filters: ReportFilters) -> list[dict]:
    stmt = select(OperationDiagnosticEventRecord)
    if filters.session_id:
        stmt = stmt.where(OperationDiagnosticEventRecord.session_id == filters.session_id)
    if filters.sku:
        stmt = stmt.where(OperationDiagnosticEventRecord.sku == filters.sku)
    if filters.days:
        cutoff = datetime.utcnow() - timedelta(days=max(1, filters.days))
        stmt = stmt.where(OperationDiagnosticEventRecord.created_at >= cutoff)
    stmt = stmt.order_by(OperationDiagnosticEventRecord.created_at.desc()).limit(500)
    return [event_to_dict(record) for record in session.exec(stmt).all()]


def _enrich_events(events: list[dict], repeated_groups: list[dict]) -> list[dict]:
    repeated_keys = {entry["group_key"] for entry in repeated_groups if entry["sku_count"] > 1}
    enriched = []
    for event in events:
        group_key = _event_group_key(event)
        severity = classify_event_severity(event, repeated_across_skus=group_key in repeated_keys)
        enriched.append(
            event
            | {
                "severity": severity,
                "group_key": group_key,
            }
        )
    return enriched


def _repeated_failures(events: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if str(event.get("status") or "") not in {"failed", "blocked", "warning"}:
            continue
        groups[_event_group_key(event)].append(event)
    repeated = []
    for key, grouped in groups.items():
        if len(grouped) < 2:
            continue
        skus = sorted({str(event.get("sku") or "") for event in grouped if str(event.get("sku") or "")})
        repeated.append(
            {
                "group_key": key,
                "count": len(grouped),
                "sku_count": len(skus),
                "affected_skus": skus,
                "first_seen": min(event.get("created_at") or "" for event in grouped),
                "last_seen": max(event.get("created_at") or "" for event in grouped),
                "severity": "critical" if len(skus) > 1 else "high",
                "sample_safe_message": str(grouped[0].get("safe_message") or ""),
            }
        )
    repeated.sort(key=lambda entry: (entry["severity"] != "critical", -entry["count"], -entry["sku_count"]))
    return repeated[:20]


def _event_group_key(event: dict) -> str:
    return "|".join(
        [
            str(event.get("operation_name") or ""),
            str(event.get("error_family") or ""),
            str(event.get("error_code") or ""),
            str(event.get("stage") or ""),
        ]
    )


def _top_counts(events: list[dict], key: str, *, limit: int = 10) -> list[dict]:
    counter = Counter(str(event.get(key) or "") for event in events if str(event.get(key) or ""))
    return [{key: name, "count": count} for name, count in counter.most_common(limit)]


def _suspected_root_causes(events: list[dict], repeated_failures: list[dict]) -> list[str]:
    causes: list[str] = []
    if any(entry["severity"] == "critical" for entry in repeated_failures):
        causes.append("A repeated failure signature is affecting multiple SKUs, suggesting a shared systemic cause.")
    family_counts = Counter(str(event.get("error_family") or "") for event in events if str(event.get("error_family") or ""))
    for family, count in family_counts.most_common(3):
        if family:
            causes.append(f"Error family '{family}' appears {count} time(s) and is a likely root-cause cluster.")
    return causes[:5]


def _recommended_actions(events: list[dict], repeated_failures: list[dict]) -> list[str]:
    actions: list[str] = []
    for repeated in repeated_failures:
        if repeated["severity"] == "critical":
            actions.append(f"Investigate repeated failure group '{repeated['group_key']}' before retrying related operations.")
    for event in events:
        action = str(event.get("recommended_next_action") or "").strip()
        if action and action not in actions:
            actions.append(action)
    return actions[:10]


def _related_files_services(events: list[dict]) -> list[str]:
    files: list[str] = []
    for event in events:
        for path in event.get("related_files_services") or []:
            if path not in files:
                files.append(path)
    return files[:20]


def _sanitized_raw_examples(events: list[dict]) -> list[dict]:
    examples = []
    for event in events:
        payload = event.get("raw_error_payload")
        if not payload and not event.get("raw_error_summary"):
            continue
        examples.append(
            {
                "event_id": event.get("event_id"),
                "operation_name": event.get("operation_name"),
                "error_family": event.get("error_family"),
                "error_code": event.get("error_code"),
                "raw_error_summary": event.get("raw_error_summary"),
                "raw_error_payload": payload,
            }
        )
        if len(examples) >= 5:
            break
    return examples


def _git_context() -> dict:
    context = {
        "current_commit_hash": "",
        "branch": "",
        "dirty_working_tree": None,
        "latest_commit_subject": "",
        "app_diagnostic_version": DIAGNOSTIC_REPORT_VERSION,
        "git_available": False,
    }
    try:
        context["current_commit_hash"] = _run_git("rev-parse HEAD")
        context["branch"] = _run_git("rev-parse --abbrev-ref HEAD")
        context["latest_commit_subject"] = _run_git("log -1 --pretty=%s")
        status = _run_git("status --porcelain")
        context["dirty_working_tree"] = bool(status.strip())
        context["git_available"] = True
    except Exception:
        pass
    return context


def _run_git(args: str) -> str:
    repo_root = get_settings().db_path.parent.parent
    completed = subprocess.run(
        ["git", *args.split()],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return completed.stdout.strip()


def _reports_dir() -> Path:
    settings = get_settings()
    return settings.db_path.parent / "diagnostic_reports"
