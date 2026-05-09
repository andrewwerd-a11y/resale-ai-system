from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import diagnostics, ebay, items
from apps.api.src.services.operation_diagnostics import (
    classify_ebay_error_payload,
    record_failure,
    record_success,
    sanitize_error_payload,
)
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    app.include_router(diagnostics.router, prefix="/api/diagnostics", tags=["diagnostics"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "operation_diagnostics.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item(**overrides) -> None:
    base = dict(
        sku="BK-000008",
        status=ItemStatus.APPROVED,
        title_raw="Diagnostic raw title",
        title_final="Diagnostic title",
        description_final="Diagnostic description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        item_specifics={},
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def test_operation_event_can_be_recorded_and_read(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    with Session(sqlite_db.engine) as session:
        event = record_success(
            session,
            operation_name="test_operation",
            route="/api/test",
            sku="BK-000008",
            safe_message="Recorded successfully.",
            result_context={"ok": True},
        )

    with _client() as client:
        recent = client.get("/api/diagnostics/events/recent")
        by_sku = client.get("/api/diagnostics/events/sku/BK-000008")
        by_id = client.get(f"/api/diagnostics/events/{event.event_id}")

    assert recent.status_code == 200
    assert recent.json()["read_only"] is True
    assert recent.json()["events"][0]["event_id"] == event.event_id
    assert by_sku.status_code == 200
    assert by_sku.json()["events"][0]["sku"] == "BK-000008"
    assert by_id.status_code == 200
    assert by_id.json()["event"]["operation_name"] == "test_operation"


def test_operation_diagnostics_sanitizes_secrets() -> None:
    payload = {
        "Authorization": "Bearer super-secret-token",
        "refresh_token": "refresh-secret",
        "shippingAddress": {"line1": "123 Main St", "postalCode": "12345"},
        "message": "access_token=abc123 4111111111111111",
    }

    sanitized = sanitize_error_payload(payload)
    serialized = str(sanitized)

    assert "super-secret-token" not in serialized
    assert "refresh-secret" not in serialized
    assert "123 Main" not in serialized
    assert "4111111111111111" not in serialized
    assert "[REDACTED]" in serialized


def test_ebay_error_payload_is_classified_and_saved_safely(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    raw = {
        "errors": [
            {
                "errorId": 25021,
                "message": "The provided condition id is invalid.",
                "access_token": "secret-token",
            }
        ]
    }
    classification = classify_ebay_error_payload(raw)
    assert classification["error_code"] == "invalid_category_condition"

    with Session(sqlite_db.engine) as session:
        event = record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/{sku}",
            sku="BK-000008",
            safe_message="eBay publish failed.",
            external_service="ebay",
            stage="publish_offer",
            error_family=classification["error_family"],
            error_code=classification["error_code"],
            raw_error_payload=classification["raw_error_payload"],
        )

    with _client() as client:
        resp = client.get(f"/api/diagnostics/events/{event.event_id}")

    assert resp.status_code == 200
    body = resp.json()["event"]
    assert body["external_service"] == "ebay"
    assert body["error_code"] == "invalid_category_condition"
    assert "secret-token" not in str(body)


def test_photo_hosting_failure_records_event(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "demo-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "demo-secret")
    core_config.get_settings.cache_clear()
    local_photo = tmp_path / "BK-000008-01.jpg"
    local_photo.write_bytes(b"photo")
    _seed_item(image_paths=[str(local_photo)])

    monkeypatch.setattr(
        "packages.ebay.src.photo_uploader.PhotoUploader.upload",
        lambda *_args, **_kwargs: Result.failure("cloudinary access_token=secret failed", error_code="CLOUDINARY_ERROR"),
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000008/photos/host")
        events = client.get("/api/diagnostics/events/sku/BK-000008")

    assert resp.status_code == 502
    event = events.json()["events"][0]
    assert event["operation_name"] == "photo_hosting"
    assert event["status"] == "failed"
    assert event["external_service"] == "cloudinary"
    assert event["mutation_attempted"] is True
    assert "secret" not in str(event).lower()


def test_publish_failure_records_operation_event(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    def fake_publish(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body='{"errors":[{"errorId":25021,"message":"invalid condition","access_token":"secret"}]}',
            stage="publish_offer",
            category_id="29223",
            local_condition_id="5000",
            inventory_condition_enum="USED_GOOD",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")
        events = client.get("/api/diagnostics/events/sku/BK-000008")

    assert resp.status_code == 500
    event = events.json()["events"][0]
    assert event["operation_name"] == "ebay_publish"
    assert event["external_service"] == "ebay"
    assert event["stage"] == "publish_offer"
    assert event["error_code"] == "invalid_category_condition"
    assert event["ebay_mutation_attempted"] is True
    assert "secret" not in str(event).lower()


def test_diagnostics_event_routes_are_read_only(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    with Session(sqlite_db.engine) as session:
        record_failure(
            session,
            operation_name="local_check",
            status="warning",
            route="/api/test",
            sku="BK-000008",
            safe_message="Warning only.",
            external_service="local",
        )

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("read-only diagnostics route must not mutate external services")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", fail_mutation)

    with _client() as client:
        recent = client.get("/api/diagnostics/events/recent")
        query = client.post("/api/diagnostics/events/query", json={"sku": "BK-000008"})

    assert recent.status_code == 200
    assert query.status_code == 200
    assert recent.json()["no_mutation_performed"] is True
    assert query.json()["events"][0]["sku"] == "BK-000008"
