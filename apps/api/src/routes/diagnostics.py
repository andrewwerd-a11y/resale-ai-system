from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

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
