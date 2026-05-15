from __future__ import annotations

import json
import re
import traceback
from datetime import datetime
from typing import Any

from sqlmodel import Session, desc, select

from packages.data.src.models.operation_diagnostic_event_record import OperationDiagnosticEventRecord

SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "api-secret",
    "api_secret",
    "secret",
    "password",
    "cookie",
    "set-cookie",
    "payment",
    "card",
    "cvv",
    "address",
    "postal",
    "zip",
    "environment",
    "env",
)

RELATED_FILES_BY_FAMILY = {
    "ebay": [
        "packages/ebay/src/inventory_client.py",
        "apps/api/src/routes/ebay.py",
        "apps/api/src/services/publish_repair.py",
    ],
    "cloudinary": [
        "packages/ebay/src/photo_uploader.py",
        "apps/api/src/routes/items.py",
        "apps/api/src/services/publish_readiness.py",
    ],
    "database": [
        "packages/data/src/db/sqlite.py",
        "packages/data/src/repositories/item_repo.py",
    ],
    "local": [
        "apps/api/src/routes/ebay.py",
        "apps/api/src/routes/items.py",
        "apps/api/src/routes/listings.py",
    ],
    "unknown": [
        "apps/api/src/services/operation_diagnostics.py",
    ],
}

EBAY_ERROR_CLASSIFICATIONS = {
    "25021": {
        "error_family": "invalid_category_condition",
        "error_code": "invalid_category_condition",
        "recommended_next_action": "Run read-only publish diagnostics and repair category/condition state before retrying publish.",
    },
    "25002": {
        "error_family": "existing_offer",
        "error_code": "offer_already_exists",
        "recommended_next_action": "Inspect the existing offer and reconcile local offer_id before retrying publish.",
    },
    "25013": {
        "error_family": "missing_inventory_item",
        "error_code": "inventory_item_not_found",
        "recommended_next_action": "Rebuild or refresh the inventory item before retrying publish.",
    },
}


def record_operation_event(
    session: Session,
    *,
    operation_name: str,
    status: str,
    safe_message: str,
    route: str | None = None,
    sku: str | None = None,
    batch_id: str | None = None,
    session_id: str | None = None,
    mutation_attempted: bool = False,
    mutation_succeeded: bool = False,
    ebay_mutation_attempted: bool = False,
    ebay_mutation_succeeded: bool = False,
    external_service: str | None = None,
    stage: str | None = None,
    error_family: str | None = None,
    error_code: str | None = None,
    raw_error_summary: str | None = None,
    raw_error_payload: Any = None,
    recommended_next_action: str | None = None,
    related_files_services: list[str] | None = None,
    request_context: dict | None = None,
    result_context: dict | None = None,
) -> OperationDiagnosticEventRecord:
    family = error_family or _family_for_service(external_service)
    record = OperationDiagnosticEventRecord(
        operation_name=operation_name,
        route=route,
        sku=_normalize_sku(sku),
        batch_id=batch_id,
        session_id=session_id,
        status=status,
        mutation_attempted=mutation_attempted,
        mutation_succeeded=mutation_succeeded,
        ebay_mutation_attempted=ebay_mutation_attempted,
        ebay_mutation_succeeded=ebay_mutation_succeeded,
        external_service=external_service or "unknown",
        stage=stage,
        error_family=family,
        error_code=error_code,
        raw_error_summary=_safe_text(raw_error_summary),
        raw_error_payload_json=_json_dumps(sanitize_error_payload(raw_error_payload)) if raw_error_payload is not None else None,
        safe_message=_safe_text(safe_message, limit=1000) or "Operation diagnostic event recorded.",
        recommended_next_action=_safe_text(recommended_next_action),
        related_files_services_json=_json_dumps(related_files_services or RELATED_FILES_BY_FAMILY.get(family or "unknown", [])),
        request_context_json=_json_dumps(sanitize_error_payload(request_context or {})),
        result_context_json=_json_dumps(sanitize_error_payload(result_context or {})),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def record_success(
    session: Session,
    *,
    operation_name: str,
    safe_message: str,
    route: str | None = None,
    sku: str | None = None,
    batch_id: str | None = None,
    session_id: str | None = None,
    mutation_attempted: bool = False,
    mutation_succeeded: bool = False,
    ebay_mutation_attempted: bool = False,
    ebay_mutation_succeeded: bool = False,
    external_service: str | None = None,
    stage: str | None = None,
    related_files_services: list[str] | None = None,
    result_context: dict | None = None,
    request_context: dict | None = None,
) -> OperationDiagnosticEventRecord:
    return record_operation_event(
        session,
        operation_name=operation_name,
        status="success",
        safe_message=safe_message,
        route=route,
        sku=sku,
        batch_id=batch_id,
        session_id=session_id,
        mutation_attempted=mutation_attempted,
        mutation_succeeded=mutation_succeeded,
        ebay_mutation_attempted=ebay_mutation_attempted,
        ebay_mutation_succeeded=ebay_mutation_succeeded,
        external_service=external_service,
        stage=stage,
        related_files_services=related_files_services,
        request_context=request_context,
        result_context=result_context,
    )


def record_failure(
    session: Session,
    *,
    operation_name: str,
    safe_message: str,
    route: str | None = None,
    sku: str | None = None,
    batch_id: str | None = None,
    session_id: str | None = None,
    status: str = "failed",
    mutation_attempted: bool = False,
    mutation_succeeded: bool = False,
    ebay_mutation_attempted: bool = False,
    ebay_mutation_succeeded: bool = False,
    external_service: str | None = None,
    stage: str | None = None,
    error_family: str | None = None,
    error_code: str | None = None,
    raw_error_summary: str | None = None,
    raw_error_payload: Any = None,
    recommended_next_action: str | None = None,
    related_files_services: list[str] | None = None,
    request_context: dict | None = None,
    result_context: dict | None = None,
) -> OperationDiagnosticEventRecord:
    return record_operation_event(
        session,
        operation_name=operation_name,
        status=status,
        safe_message=safe_message,
        route=route,
        sku=sku,
        batch_id=batch_id,
        session_id=session_id,
        mutation_attempted=mutation_attempted,
        mutation_succeeded=mutation_succeeded,
        ebay_mutation_attempted=ebay_mutation_attempted,
        ebay_mutation_succeeded=ebay_mutation_succeeded,
        external_service=external_service,
        stage=stage,
        error_family=error_family,
        error_code=error_code,
        raw_error_summary=raw_error_summary,
        raw_error_payload=raw_error_payload,
        recommended_next_action=recommended_next_action,
        related_files_services=related_files_services,
        request_context=request_context,
        result_context=result_context,
    )


def classify_exception(exc: Exception) -> dict:
    text = str(exc)
    lower = text.lower()
    if "sqlite" in lower or "database" in lower:
        family = "database"
        service = "database"
        action = "Inspect the local database state and retry after resolving the storage error."
    elif "cloudinary" in lower or "photo" in lower or "image" in lower:
        family = "photo_hosting"
        service = "cloudinary"
        action = "Verify photo hosting configuration and local image files before retrying."
    elif "ebay" in lower or "offer" in lower or "inventory" in lower:
        family = "ebay"
        service = "ebay"
        action = "Run read-only eBay diagnostics before retrying the operation."
    else:
        family = "unexpected_exception"
        service = "unknown"
        action = "Review the operation context and server logs before retrying."
    return {
        "external_service": service,
        "error_family": family,
        "error_code": exc.__class__.__name__,
        "raw_error_summary": _truncate(text),
        "safe_message": f"{exc.__class__.__name__}: {_truncate(text, limit=300)}",
        "recommended_next_action": action,
        "traceback_summary": _truncate("".join(traceback.format_exception_only(type(exc), exc)), limit=1000),
    }


def classify_ebay_error_payload(payload: Any) -> dict:
    sanitized = sanitize_error_payload(payload)
    error_ids = _extract_ebay_error_ids(sanitized)
    first_id = error_ids[0] if error_ids else ""
    known = EBAY_ERROR_CLASSIFICATIONS.get(first_id, {})
    if known:
        return {
            "external_service": "ebay",
            "error_family": known["error_family"],
            "error_code": known["error_code"],
            "recommended_next_action": known["recommended_next_action"],
            "raw_error_summary": _truncate(_summarize_payload(sanitized)),
            "raw_error_payload": sanitized,
            "ebay_error_ids": error_ids,
        }
    text_known = _classify_ebay_error_text(_summarize_payload(sanitized))
    if text_known:
        return {
            "external_service": "ebay",
            "error_family": text_known["error_family"],
            "error_code": text_known["error_code"],
            "recommended_next_action": text_known["recommended_next_action"],
            "raw_error_summary": _truncate(_summarize_payload(sanitized)),
            "raw_error_payload": sanitized,
            "ebay_error_ids": error_ids,
        }
    return {
        "external_service": "ebay",
        "error_family": "ebay_api_error" if error_ids else "ebay_unknown_error",
        "error_code": first_id or "EBAY_ERROR",
        "recommended_next_action": "Run read-only eBay diagnostics and review the sanitized payload before retrying.",
        "raw_error_summary": _truncate(_summarize_payload(sanitized)),
        "raw_error_payload": sanitized,
        "ebay_error_ids": error_ids,
    }


def _classify_ebay_error_text(summary: str) -> dict:
    lower = (summary or "").lower()
    if "invalid access token" in lower or "expired access token" in lower or "authorization" in lower and "invalid" in lower:
        return {
            "error_family": "auth",
            "error_code": "expired_or_invalid_access_token",
            "recommended_next_action": "Reconnect eBay OAuth or replace the active token before retrying publish.",
        }
    if "insufficient scope" in lower or "insufficient permissions" in lower or "scope" in lower and "permission" in lower:
        return {
            "error_family": "auth",
            "error_code": "insufficient_scope",
            "recommended_next_action": "Reconnect eBay OAuth with the required inventory and account scopes.",
        }
    if "fulfillment policy" in lower or "payment policy" in lower or "return policy" in lower or "listing policy" in lower:
        return {
            "error_family": "seller_policy",
            "error_code": "seller_policy_missing_or_invalid",
            "recommended_next_action": "Configure valid fulfillment, payment, and return policies before retrying publish.",
        }
    if "merchant location" in lower or "merchantlocationkey" in lower or "inventory location" in lower:
        return {
            "error_family": "merchant_location",
            "error_code": "merchant_location_invalid",
            "recommended_next_action": "Configure or verify the eBay inventory merchant location key before retrying publish.",
        }
    if "image url" in lower or "picture url" in lower or "imageurl" in lower:
        return {
            "error_family": "photo_hosting",
            "error_code": "invalid_image_url",
            "recommended_next_action": "Rehost or correct public photo URLs before retrying publish.",
        }
    if "inventory item" in lower and ("not found" in lower or "missing" in lower):
        return {
            "error_family": "missing_inventory_item",
            "error_code": "inventory_item_not_found",
            "recommended_next_action": "Recreate or refresh the inventory item before publishing the offer.",
        }
    if "offer" in lower and ("not found" in lower or "missing" in lower):
        return {
            "error_family": "stale_offer",
            "error_code": "offer_not_found",
            "recommended_next_action": "Recreate the offer or clear the stale local offer_id after explicit approval.",
        }
    if "already published" in lower or "duplicate listing" in lower:
        return {
            "error_family": "duplicate_publish_risk",
            "error_code": "already_published",
            "recommended_next_action": "Do not publish again; sync listing state or use revise/update flow.",
        }
    if "missing required" in lower and "aspect" in lower:
        return {
            "error_family": "category_aspects",
            "error_code": "missing_required_aspects",
            "recommended_next_action": "Populate missing required category aspects before retrying publish.",
        }
    if ("aspect" in lower and "too long" in lower) or ("aspect" in lower and "maximum" in lower) or ("invalid aspect" in lower):
        return {
            "error_family": "category_aspects",
            "error_code": "invalid_aspect_value",
            "recommended_next_action": "Correct invalid or overlong category aspect values before retrying publish.",
        }
    return {}


def sanitize_error_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        stripped = payload.strip()
        if stripped.startswith(("{", "[")):
            try:
                return _bound(_sanitize(json.loads(stripped)))
            except json.JSONDecodeError:
                pass
    return _bound(_sanitize(payload))


def list_recent_events(session: Session, *, limit: int = 50) -> list[dict]:
    safe_limit = max(1, min(int(limit or 50), 200))
    records = session.exec(
        select(OperationDiagnosticEventRecord)
        .order_by(desc(OperationDiagnosticEventRecord.created_at))
        .limit(safe_limit)
    ).all()
    return [event_to_dict(record) for record in records]


def list_events_for_sku(session: Session, sku: str, *, limit: int = 50) -> list[dict]:
    normalized = _normalize_sku(sku)
    safe_limit = max(1, min(int(limit or 50), 200))
    records = session.exec(
        select(OperationDiagnosticEventRecord)
        .where(OperationDiagnosticEventRecord.sku == normalized)
        .order_by(desc(OperationDiagnosticEventRecord.created_at))
        .limit(safe_limit)
    ).all()
    return [event_to_dict(record) for record in records]


def get_event(session: Session, event_id: str) -> dict | None:
    record = session.get(OperationDiagnosticEventRecord, event_id)
    return event_to_dict(record) if record else None


def query_events(
    session: Session,
    *,
    sku: str | None = None,
    operation_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    stmt = select(OperationDiagnosticEventRecord)
    if sku:
        stmt = stmt.where(OperationDiagnosticEventRecord.sku == _normalize_sku(sku))
    if operation_name:
        stmt = stmt.where(OperationDiagnosticEventRecord.operation_name == operation_name)
    if status:
        stmt = stmt.where(OperationDiagnosticEventRecord.status == status)
    stmt = stmt.order_by(desc(OperationDiagnosticEventRecord.created_at)).limit(max(1, min(int(limit or 50), 200)))
    return [event_to_dict(record) for record in session.exec(stmt).all()]


def event_to_dict(record: OperationDiagnosticEventRecord) -> dict:
    return {
        "event_id": record.event_id,
        "created_at": _dt(record.created_at),
        "operation_name": record.operation_name,
        "route": record.route,
        "sku": record.sku,
        "batch_id": record.batch_id,
        "session_id": record.session_id,
        "status": record.status,
        "mutation_attempted": record.mutation_attempted,
        "mutation_succeeded": record.mutation_succeeded,
        "ebay_mutation_attempted": record.ebay_mutation_attempted,
        "ebay_mutation_succeeded": record.ebay_mutation_succeeded,
        "external_service": record.external_service,
        "stage": record.stage,
        "error_family": record.error_family,
        "error_code": record.error_code,
        "raw_error_summary": record.raw_error_summary,
        "raw_error_payload": _json_loads(record.raw_error_payload_json),
        "safe_message": record.safe_message,
        "recommended_next_action": record.recommended_next_action,
        "related_files_services": _json_loads(record.related_files_services_json) or [],
        "request_context": _json_loads(record.request_context_json) or {},
        "result_context": _json_loads(record.result_context_json) or {},
    }


def _extract_ebay_error_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    parsed = payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            ids.extend(re.findall(r"\b(?:errorId|error)\D{0,20}(\d{4,})\b", payload, flags=re.IGNORECASE))
            ids.extend(re.findall(r"\b(25\d{3}|219\d{5})\b", payload))
            return list(dict.fromkeys(ids))
    for node in _walk(parsed):
        if isinstance(node, dict):
            for key in ("errorId", "error_id", "errorCode", "code"):
                value = node.get(key)
                if value is not None and str(value).strip().isdigit():
                    ids.append(str(value).strip())
    return list(dict.fromkeys(ids))


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _sanitize(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, key=key) for v in value]
    if isinstance(value, tuple):
        return [_sanitize(v, key=key) for v in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    text = value
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(access[_-]?token|refresh[_-]?token|api[_-]?key|api[_-]?secret|password)=([^&\s]+)", r"\1=[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{12,19}\b", "[REDACTED_CARD_OR_TOKEN]", text)
    return _truncate(text, limit=2000) or ""


def _safe_text(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    return _truncate(_sanitize_text(str(value)), limit=limit)


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in SENSITIVE_KEY_PARTS)


def _bound(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {k: _bound(v, depth=depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_bound(v, depth=depth + 1) for v in value[:50]]
    if isinstance(value, str):
        return _truncate(value, limit=2000)
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _summarize_payload(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _truncate(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _normalize_sku(sku: str | None) -> str | None:
    normalized = str(sku or "").strip().upper()
    return normalized or None


def _family_for_service(service: str | None) -> str | None:
    if service in {"ebay", "cloudinary", "database", "local"}:
        return service
    return None


def _dt(value: datetime | None) -> str:
    return value.isoformat() if value else ""
