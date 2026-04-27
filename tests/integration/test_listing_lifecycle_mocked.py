from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from apps.api.src.routes import ebay, reports
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from packages.data.src.models.sale_record import SaleRecord


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "listing_lifecycle.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    # Keep auth "configured" for revise route checks without any live call.
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")

    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()
    return db_path


def _seed_item(sku: str, status: str) -> None:
    with Session(sqlite_db.engine) as session:
        repo = ItemRepository(session)
        repo.upsert(
            Item(
                sku=sku,
                status=status,
                title_raw=f"{sku} raw",
                title_final=f"{sku} title",
                description_final=f"{sku} description",
                list_price=20.0,
                cost=5.0,
                shipping_cost=2.0,
                category_key="books",
                ebay_category_id="29223",
                condition_id="5000",
                image_paths=[],
            )
        )


def _get_item(sku: str) -> Item | None:
    with Session(sqlite_db.engine) as session:
        return ItemRepository(session).get_by_sku(sku)


def test_publish_success_sets_listed_and_listing_fields_for_approved_sku(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.APPROVED)

    def fake_publish(_self, item):
        return Result.success(
            {
                "listing_id": f"L-{item.sku}",
                "listing_url": f"https://example.test/{item.sku}",
                "offer_id": f"O-{item.sku}",
                "photo_urls": [],
            }
        )

    stats_calls: list[tuple[str, bool, float | None]] = []

    def fake_update_stats(_self, category_id, item, sold=False, sold_price=None):
        stats_calls.append((category_id, sold, sold_price))

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish)
    monkeypatch.setattr(
        "packages.ebay.src.category_spreadsheet.CategorySpreadsheet.update_field_stats",
        fake_update_stats,
    )

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000005")

    assert resp.status_code == 200
    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.LISTED
    assert item.listing_id == "L-BK-000005"
    assert item.listing_url == "https://example.test/BK-000005"
    assert stats_calls == [("29223", False, None)]


def test_publish_failure_does_not_write_listing_or_listed_status(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000008", ItemStatus.APPROVED)

    def fake_publish_fail(_self, _item):
        return Result.failure("mocked publish failure", error_code="API_ERROR")

    stats_calls: list[tuple[str, bool, float | None]] = []

    def fake_update_stats(_self, category_id, item, sold=False, sold_price=None):
        stats_calls.append((category_id, sold, sold_price))

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)
    monkeypatch.setattr(
        "packages.ebay.src.category_spreadsheet.CategorySpreadsheet.update_field_stats",
        fake_update_stats,
    )

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 500
    item = _get_item("BK-000008")
    assert item is not None
    assert item.status == ItemStatus.APPROVED
    assert item.listing_id is None
    assert item.listing_url is None
    assert stats_calls == []


def test_revise_failure_does_not_persist_local_update_or_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.LISTED)
    with Session(sqlite_db.engine) as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku("BK-000005")
        assert item is not None
        item.listing_id = "LIVE-1"
        item.listing_url = "https://example.test/live/1"
        before_price = item.list_price
        before_listing_id = item.listing_id
        before_listing_url = item.listing_url
        repo.upsert(item)

    class _Resp:
        status_code = 500
        text = "mock revise failed"

    monkeypatch.setattr("apps.api.src.routes.ebay.ebay_http.put", lambda *a, **k: _Resp())

    with _client() as client:
        resp = client.patch("/api/ebay/listing/BK-000005", json={"list_price": 99.99})

    assert resp.status_code == 502
    item = _get_item("BK-000005")
    assert item is not None
    assert item.list_price == before_price
    assert item.listing_id == before_listing_id
    assert item.listing_url == before_listing_url
    assert item.status == ItemStatus.LISTED


def test_revise_invalid_token_returns_structured_auth_error(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.LISTED)

    class _Resp:
        status_code = 401
        text = "Error 1001: Invalid access token. Check the value of the Authorization HTTP request header."

    monkeypatch.setattr("apps.api.src.routes.ebay.ebay_http.put", lambda *a, **k: _Resp())

    with _client() as client:
        resp = client.patch("/api/ebay/listing/BK-000005", json={"list_price": 99.99})

    assert resp.status_code == 502
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "expired_or_invalid_access_token"
    assert detail.get("category") == "auth"
    assert "token" in detail.get("message", "").lower()


def test_mark_sold_creates_sale_record_updates_profit_and_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000009", ItemStatus.LISTED)
    monkeypatch.setattr(
        "packages.sync.src.cross_platform_sync._load_platforms",
        lambda: {"ebay": {"active": True, "label": "eBay", "end_listing_supported": True}},
    )
    stats_calls: list[tuple[str, bool, float | None]] = []

    def fake_update_stats(_self, category_id, item, sold=False, sold_price=None):
        stats_calls.append((category_id, sold, sold_price))

    monkeypatch.setattr(
        "packages.ebay.src.category_spreadsheet.CategorySpreadsheet.update_field_stats",
        fake_update_stats,
    )

    with Session(sqlite_db.engine) as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku("BK-000009")
        assert item is not None
        item.listing_id = "LIST-9"
        repo.upsert(item)

    with _client() as client:
        sold = client.post("/api/ebay/mark-sold/BK-000009?sold_price=30.0&fees=3.0&platform=ebay")
        assert sold.status_code == 200

        sales = client.get("/api/reports/sales")
        summary = client.get("/api/reports/summary")

    assert sales.status_code == 200
    sales_rows = sales.json()
    assert any(row.get("sku") == "BK-000009" for row in sales_rows)

    item = _get_item("BK-000009")
    assert item is not None
    assert item.status == ItemStatus.SOLD
    assert round(item.net_profit or 0.0, 2) == 20.0  # 30 - 5 - 3 - 2
    assert round(item.profit_margin or 0.0, 4) == round(20.0 / 30.0, 4)

    with Session(sqlite_db.engine) as session:
        recs = session.exec(select(SaleRecord)).all()
        assert len(recs) == 1
        assert recs[0].sku == "BK-000009"
    assert stats_calls == [("29223", True, 30.0)]

    assert summary.status_code == 200
    summary_json = summary.json()
    assert summary_json["total_sales"] == 1
    assert summary_json["total_net_profit"] == 20.0


def test_publish_success_still_returns_200_when_category_stats_update_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.APPROVED)

    def fake_publish(_self, item):
        return Result.success(
            {
                "listing_id": f"L-{item.sku}",
                "listing_url": f"https://example.test/{item.sku}",
                "offer_id": f"O-{item.sku}",
                "photo_urls": [],
            }
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish)
    monkeypatch.setattr(
        "packages.ebay.src.category_spreadsheet.CategorySpreadsheet.update_field_stats",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stats write failed")),
    )

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000005")

    assert resp.status_code == 200
    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.LISTED


def test_non_approved_sku_blocked_under_route_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-999999")

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")
