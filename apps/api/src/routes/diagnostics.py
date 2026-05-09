from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from apps.api.src.services.diagnostic_reports import (
    generate_report,
    generate_session_report,
    generate_sku_report,
    generate_weekly_report,
    list_recent_reports,
)
from apps.api.src.services.operation_diagnostics import (
    get_event,
    list_events_for_sku,
    list_recent_events,
    query_events,
)
from packages.data.src.db.sqlite import get_session

router = APIRouter()


class OperationDiagnosticsQuery(BaseModel):
    sku: str | None = None
    operation_name: str | None = None
    status: str | None = None
    limit: int = 50


class DiagnosticReportGenerateRequest(BaseModel):
    report_type: str
    session_id: str | None = None
    sku: str | None = None
    days: int | None = None
    persist: bool = True


@router.get("/events/recent")
def get_recent_operation_events(
    limit: int = 50,
    session: Session = Depends(get_session),
):
    return {
        "events": list_recent_events(session, limit=limit),
        "read_only": True,
        "no_mutation_performed": True,
    }


@router.get("/events/sku/{sku}")
def get_operation_events_for_sku(
    sku: str,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    return {
        "sku": (sku or "").strip().upper(),
        "events": list_events_for_sku(session, sku, limit=limit),
        "read_only": True,
        "no_mutation_performed": True,
    }


@router.get("/reports/recent")
def get_recent_diagnostic_reports(limit: int = 20):
    return {
        "reports": list_recent_reports(limit=limit),
        "read_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_external_send": True,
    }


@router.get("/reports/weekly")
def get_weekly_diagnostic_report(
    days: int = 7,
    session: Session = Depends(get_session),
):
    return {
        "report": generate_weekly_report(session, days=days),
        "read_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_external_send": True,
    }


@router.get("/reports/session/{session_id}")
def get_session_diagnostic_report(
    session_id: str,
    session: Session = Depends(get_session),
):
    return {
        "report": generate_session_report(session, session_id),
        "read_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_external_send": True,
    }


@router.get("/reports/sku/{sku}")
def get_sku_diagnostic_report(
    sku: str,
    session: Session = Depends(get_session),
):
    return {
        "report": generate_sku_report(session, sku),
        "read_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_external_send": True,
    }


@router.post("/reports/generate")
def post_generate_diagnostic_report(
    payload: DiagnosticReportGenerateRequest,
    session: Session = Depends(get_session),
):
    try:
        report = generate_report(
            session,
            report_type=payload.report_type,
            session_id=payload.session_id,
            sku=payload.sku,
            days=payload.days,
            persist=payload.persist,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "report": report,
        "read_only": False,
        "local_persistence_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_external_send": True,
    }


@router.get("/events/{event_id}")
def get_operation_event(
    event_id: str,
    session: Session = Depends(get_session),
):
    event = get_event(session, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Operation diagnostic event {event_id} not found")
    return {
        "event": event,
        "read_only": True,
        "no_mutation_performed": True,
    }


@router.post("/events/query")
def query_operation_events(
    payload: OperationDiagnosticsQuery,
    session: Session = Depends(get_session),
):
    return {
        "events": query_events(
            session,
            sku=payload.sku,
            operation_name=payload.operation_name,
            status=payload.status,
            limit=payload.limit,
        ),
        "read_only": True,
        "no_mutation_performed": True,
    }
