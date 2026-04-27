from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import listings
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "publish_preview.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item(**overrides):
    base = dict(
        sku="BK-000005",
        status=ItemStatus.APPROVED,
        title_final="Preview title",
        description_final="Preview description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=["https://images.example.test/BK-000005-01.jpg"],
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_mutations(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("unexpected mutation during publish preview")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload_all", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key", _fail)


def test_publish_preview_returns_payload_shape_for_ready_sku(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item()

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["would_publish"] is True
    assert body["mutation_allowed"] is False
    assert body["inventory_item_payload_preview"]["product"]["title"] == "Preview title"
    assert body["offer_payload_preview"]["sku"] == "BK-000005"
    assert body["offer_payload_preview"]["merchantLocationKey"] == "preview-location"
    assert body["offer_payload_preview"]["listingPolicies"]["fulfillmentPolicyId"] == "fulfillment-1"


def test_publish_preview_marks_unready_sku_as_not_publishable(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(title_final="", description_final="", list_price=None, ebay_category_id="", condition_id="", image_paths=[])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["would_publish"] is False
    assert "Missing required field: title." in body["readiness"]["blockers"]


def test_publish_preview_respects_e2e_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(sku="BK-999999")

    with _client() as client:
        resp = client.get("/api/listings/BK-999999/publish-preview")

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")


def test_publish_preview_works_while_allow_live_e2e_is_false(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_LIVE_E2E", raising=False)
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(status=ItemStatus.LISTED, offer_id="offer-1")

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["mutation_allowed"] is False
    assert any("ALLOW_LIVE_E2E is false" in reason for reason in body["mutation_blockers"])
    assert body["revision_payload_preview"]["offer_id"] == "offer-1"


def test_publish_preview_normalizes_overlong_color_before_any_ebay_call(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(
        color="blue and white dress on a woman, various colors in the illustration background",
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["would_publish"] is True
    assert body["inventory_item_payload_preview"]["product"]["aspects"]["Color"] == ["Blue/White"]


def test_publish_preview_shows_publish_existing_offer_plan(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(
        status=ItemStatus.EXPORT_READY,
        offer_id="156719395011",
        listing_id="",
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["existing_offer_id_detected"] is True
    assert body["planned_action"] == "publish_existing_offer"
    assert body["listing_id_missing"] is True
