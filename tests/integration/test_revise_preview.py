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
    db_path = tmp_path / "revise_preview.db"
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
        status=ItemStatus.LISTED,
        title_final="Revise title",
        description_final="Revise description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        listing_id="LIST-1",
        offer_id="OFFER-1",
        listing_url="https://example.test/listings/LIST-1",
        image_paths=["https://images.example.test/BK-000005-01.jpg"],
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_mutations(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("unexpected mutation during revise preview")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload_all", _fail)


def test_revise_preview_returns_payload_for_ready_listed_sku(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item()

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/revise-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["would_revise"] is True
    assert body["mutation_allowed"] is False
    assert body["listing_identifiers"]["offer_id"] == "OFFER-1"
    assert body["inventory_item_payload_preview"]["product"]["title"] == "Revise title"
    assert body["offer_payload_preview"]["merchantLocationKey"] == "preview-location"
    assert body["revise_readiness"]["auth_readiness"]["category"] == "auth"


def test_revise_preview_blocks_when_offer_or_listing_ids_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(listing_id="", offer_id="")

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/revise-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["would_revise"] is False
    assert "Listing ID is missing." in body["revise_readiness"]["blockers"]
    assert "Offer ID is missing." in body["revise_readiness"]["blockers"]


def test_revise_preview_surfaces_missing_auth_without_500(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item()

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/revise-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["would_revise"] is False
    auth_readiness = body["revise_readiness"]["auth_readiness"]
    assert auth_readiness["code"] in {"missing_token", "sandbox_production_mismatch"}
    assert auth_readiness["category"] == "auth"


def test_revise_preview_respects_e2e_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(sku="BK-999999")

    with _client() as client:
        resp = client.get("/api/listings/BK-999999/revise-preview")

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")
