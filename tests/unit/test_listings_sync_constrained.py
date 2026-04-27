from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import listings
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


class _Resp:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self):
        return self._body


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    return TestClient(app)


def _configure_runtime(monkeypatch, tmp_path, *, guard_enabled: bool) -> None:
    db_path = tmp_path / "listings_sync_constrained.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    if guard_enabled:
        monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    else:
        monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)

    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item(sku: str, *, offer_id: str | None = None, list_price: float = 20.0) -> None:
    with Session(sqlite_db.engine) as session:
        repo = ItemRepository(session)
        repo.upsert(
            Item(
                sku=sku,
                status="listed",
                title_raw=f"{sku} raw",
                title_final=f"{sku} title",
                description_final=f"{sku} desc",
                category_key="books",
                category_label="Books",
                ebay_category_id="29223",
                condition_id="5000",
                list_price=list_price,
                offer_id=offer_id,
                image_paths=[],
            )
        )


def test_sync_constrained_approved_skus_avoids_full_pagination(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)
    _seed_item("BK-000005")
    calls: list[str] = []

    def fake_get(url: str, **kwargs):
        calls.append(url)
        if "/sell/inventory/v1/inventory_item/BK-000005" in url:
            return _Resp(200, {"sku": "BK-000005"})
        if url.endswith("/sell/inventory/v1/offer"):
            return _Resp(200, {"offers": [{"offerId": "OFFER-1"}]})
        if url.endswith("/sell/inventory/v1/inventory_item"):
            return _Resp(500, {}, "paginated path should not be used")
        return _Resp(404, {}, "not found")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", fake_get)

    with _client() as client:
        resp = client.get("/api/listings/sync", params={"skus": "BK-000005", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["constrained"] is True
    assert body["requested_skus"] == ["BK-000005"]
    assert body["synced"] == 1
    assert body["updated_offer_ids"] == 1
    assert body["updated_listing_ids"] == 0
    assert body["listing_id_available_from_sync"] is False
    assert "Publish the existing offer" in body["next_action"]
    assert not any(url.endswith("/sell/inventory/v1/inventory_item") for url in calls)


def test_sync_constrained_blocks_non_approved_sku(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)

    with _client() as client:
        resp = client.get("/api/listings/sync", params={"skus": "BK-999999", "e2e_only": "true"})

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")


def test_sync_constrained_upstream_failure_returns_clear_json(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)
    _seed_item("BK-000008")

    def fake_get(url: str, **kwargs):
        if "/sell/inventory/v1/inventory_item/BK-000005" in url:
            raise TimeoutError("upstream timeout")
        if "/sell/inventory/v1/inventory_item/BK-000008" in url:
            return _Resp(200, {"sku": "BK-000008"})
        if url.endswith("/sell/inventory/v1/offer"):
            return _Resp(500, {}, "offer error")
        return _Resp(404, {}, "not found")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", fake_get)

    with _client() as client:
        resp = client.get(
            "/api/listings/sync",
            params={"skus": "BK-000005,BK-000008", "e2e_only": "true"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["constrained"] is True
    assert body["synced"] == 1
    assert "errors" in body
    assert any("BK-000005" in err for err in body["errors"])


def test_sync_unconstrained_uses_paginated_behavior(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=False)
    _seed_item("BK-000005")
    paginated_calls = {"count": 0}

    def fake_get(url: str, **kwargs):
        if url.endswith("/sell/inventory/v1/inventory_item"):
            paginated_calls["count"] += 1
            return _Resp(200, {"inventoryItems": [{"sku": "BK-000005"}]})
        if url.endswith("/sell/inventory/v1/offer"):
            return _Resp(200, {"offers": []})
        return _Resp(404, {}, "not found")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", fake_get)

    with _client() as client:
        resp = client.get("/api/listings/sync")

    assert resp.status_code == 200
    body = resp.json()
    assert "synced" in body
    assert "updated" in body
    assert "not_found" in body
    assert "pages_fetched" in body
    assert paginated_calls["count"] >= 1


def test_sync_constrained_stores_listing_id_when_offer_includes_it(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)
    _seed_item("BK-000008", offer_id="OLD-OFFER")

    def fake_get(url: str, **kwargs):
        if "/sell/inventory/v1/inventory_item/BK-000008" in url:
            return _Resp(200, {"sku": "BK-000008"})
        if url.endswith("/sell/inventory/v1/offer"):
            return _Resp(
                200,
                {
                    "offers": [
                        {
                            "offerId": "NEW-OFFER",
                            "listingId": "123456789012",
                            "listingUrl": "https://www.sandbox.ebay.com/itm/123456789012",
                            "pricingSummary": {"price": {"value": "22.00"}},
                        }
                    ]
                },
            )
        return _Resp(404, {}, "not found")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", fake_get)

    with _client() as client:
        resp = client.get("/api/listings/sync", params={"skus": "BK-000008", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_offer_ids"] == 1
    assert body["updated_listing_ids"] == 1
    assert body["listing_id_available_from_sync"] is True

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-000008")
        assert item.offer_id == "NEW-OFFER"
        assert item.listing_id == "123456789012"
        assert item.listing_url == "https://www.sandbox.ebay.com/itm/123456789012"
