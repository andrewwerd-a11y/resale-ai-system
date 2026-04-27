from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from apps.api.src.routes import ebay, reports
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.sale_record import SaleRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from packages.ebay.src.inventory_client import _EbayApiError


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "ebay_failure_modes.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
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


def _seed_item(sku: str, status: str, *, image_paths=None) -> None:
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
                image_paths=image_paths or [],
            )
        )


def _get_item(sku: str) -> Item | None:
    with Session(sqlite_db.engine) as session:
        return ItemRepository(session).get_by_sku(sku)


def _sale_records_for_sku(sku: str) -> list[SaleRecord]:
    with Session(sqlite_db.engine) as session:
        return session.exec(select(SaleRecord).where(SaleRecord.sku == sku)).all()


def test_publish_partial_failure_inventory_ok_offer_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.APPROVED)

    calls = {"put": 0, "post": 0}
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_seller_policies",
        lambda _self: {"fulfillment_id": "f", "payment_id": "p", "return_id": "r"},
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key",
        lambda _self: "default",
    )

    def fake_put(_self, *_args, **_kwargs):
        calls["put"] += 1
        return {}

    def fake_post(_self, *_args, **kwargs):
        calls["post"] += 1
        step = kwargs.get("step", "")
        if step == "create_offer":
            raise _EbayApiError(422, "create_offer failed", "invalid category/condition")
        return {}

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fake_put)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fake_post)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000005")

    assert resp.status_code == 500
    assert "create_offer failed" in resp.json().get("detail", "")
    assert calls["put"] == 1
    assert calls["post"] == 1
    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.APPROVED
    assert item.listing_id is None
    assert item.listing_url is None


def test_publish_partial_failure_offer_ok_publish_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000008", ItemStatus.APPROVED)

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_seller_policies",
        lambda _self: {"fulfillment_id": "f", "payment_id": "p", "return_id": "r"},
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key",
        lambda _self: "default",
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient._put",
        lambda _self, *_args, **_kwargs: {},
    )

    call_count = {"post": 0}

    def fake_post(_self, *_args, **kwargs):
        call_count["post"] += 1
        step = kwargs.get("step", "")
        if step == "create_offer":
            return {"offerId": "O-BK-000008"}
        if step == "publish_offer":
            raise _EbayApiError(500, "publish_offer failed", "publish blocked")
        return {}

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fake_post)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 500
    assert "publish_offer failed" in resp.json().get("detail", "")
    assert call_count["post"] == 2
    item = _get_item("BK-000008")
    assert item is not None
    assert item.status == ItemStatus.APPROVED
    assert item.listing_id is None
    assert item.listing_url is None


def test_publish_existing_offer_recovery_persists_offer_id_when_publish_still_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000008", ItemStatus.APPROVED)

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_seller_policies",
        lambda _self: {"fulfillment_id": "f", "payment_id": "p", "return_id": "r"},
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key",
        lambda _self: "default",
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient._put",
        lambda _self, *_args, **_kwargs: {},
    )

    def fake_post(_self, *_args, **kwargs):
        step = kwargs.get("step", "")
        if step == "create_offer":
            raise _EbayApiError(
                409,
                "create_offer failed",
                '{"errors":[{"message":"Offer entity already exists","parameters":[{"name":"offerId","value":"156719395011"}]}]}',
            )
        if step == "publish_offer":
            raise _EbayApiError(500, "publish_offer failed", "publish blocked")
        return {}

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fake_post)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 500
    assert "recovered offer_id: 156719395011" in resp.json().get("detail", "")
    item = _get_item("BK-000008")
    assert item is not None
    assert item.offer_id == "156719395011"
    assert item.status == ItemStatus.APPROVED
    assert item.listing_id is None


def test_publish_existing_offer_recovery_success_stores_listing_and_offer(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000008", ItemStatus.APPROVED)

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_seller_policies",
        lambda _self: {"fulfillment_id": "f", "payment_id": "p", "return_id": "r"},
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_merchant_location_key",
        lambda _self: "default",
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient._put",
        lambda _self, *_args, **_kwargs: {},
    )

    def fake_post(_self, *_args, **kwargs):
        step = kwargs.get("step", "")
        if step == "create_offer":
            raise _EbayApiError(
                409,
                "create_offer failed",
                '{"errors":[{"message":"Offer entity already exists","parameters":[{"name":"offerId","value":"156719395011"}]}]}',
            )
        if step == "publish_offer":
            return {"listingId": "987654321012"}
        return {}

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fake_post)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 200
    body = resp.json()
    assert body["offer_id"] == "156719395011"
    assert body["recovered_existing_offer"] is True
    assert body["listing_id"] == "987654321012"
    item = _get_item("BK-000008")
    assert item is not None
    assert item.offer_id == "156719395011"
    assert item.listing_id == "987654321012"
    assert item.status == ItemStatus.LISTED


def test_publish_returns_invalid_category_condition_error_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.APPROVED)

    def fail_invalid_category(_self, _item):
        from packages.core.src.result import Result

        return Result.failure(
            "eBay API error 422: create_offer failed",
            error_code="API_ERROR",
            body="Invalid categoryId or condition for selected category",
        )

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.publish_item",
        fail_invalid_category,
    )

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000005")

    assert resp.status_code == 500
    detail = resp.json().get("detail", "")
    assert "Invalid categoryId or condition" in detail
    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.APPROVED
    assert item.listing_id is None


def test_publish_returns_invalid_photo_url_error_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.APPROVED, image_paths=["http://bad.local/not-image.jpg"])

    def fail_invalid_photo(_self, _item):
        from packages.core.src.result import Result

        return Result.failure(
            "eBay API error 400: create_inventory_item failed",
            error_code="API_ERROR",
            body="Invalid picture URL",
        )

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.publish_item",
        fail_invalid_photo,
    )

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000005")

    assert resp.status_code == 500
    assert "Invalid picture URL" in resp.json().get("detail", "")
    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.APPROVED
    assert item.listing_id is None


def test_sync_sold_unknown_sku_does_not_mutate_known_items(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.LISTED)

    def fake_orders(_self):
        return [
            {
                "pricingSummary": {"total": {"value": "30.00"}, "fee": {"value": "3.00"}},
                "lineItems": [{"sku": "BK-UNKNOWN"}],
            }
        ]

    monkeypatch.setattr("packages.ebay.src.sold_sync.SoldSync._fetch_sold_orders", fake_orders)

    with _client() as client:
        resp = client.post("/api/ebay/sync-sold", params={"skus": "BK-000005", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] == 0
    assert body["unknown_skus"] == 1
    assert body["skipped"] >= 1
    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.LISTED
    assert _sale_records_for_sku("BK-000005") == []


def test_sync_sold_rejects_non_approved_sku_under_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)

    called = {"reconcile": False}

    def fail_if_called(*_args, **_kwargs):  # pragma: no cover
        called["reconcile"] = True
        return {}

    monkeypatch.setattr("packages.ebay.src.sold_sync.SoldSync.reconcile", fail_if_called)

    with _client() as client:
        resp = client.post("/api/ebay/sync-sold", params={"skus": "BK-999999", "e2e_only": "true"})

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")
    assert called["reconcile"] is False


def test_sync_sold_duplicate_orders_do_not_duplicate_sale_records(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)

    _seed_item("BK-000009", ItemStatus.SOLD)
    with Session(sqlite_db.engine) as session:
        session.add(
            SaleRecord(
                sku="BK-000009",
                platform="ebay",
                listing_id="LIST-9",
                sold_price=30.0,
                cost=5.0,
                fees=3.0,
                shipping_cost=2.0,
                gross_profit=25.0,
                net_profit=20.0,
                gross_margin=0.8333,
                net_margin=0.6667,
                date_sold=datetime.utcnow(),
            )
        )
        session.commit()

    def fake_orders(_self):
        return [
            {
                "pricingSummary": {"total": {"value": "30.00"}, "fee": {"value": "3.00"}},
                "lineItems": [{"sku": "BK-000009"}],
            },
            {
                "pricingSummary": {"total": {"value": "30.00"}, "fee": {"value": "3.00"}},
                "lineItems": [{"sku": "BK-000009"}],
            },
        ]

    monkeypatch.setattr("packages.ebay.src.sold_sync.SoldSync._fetch_sold_orders", fake_orders)

    with _client() as client:
        resp = client.post("/api/ebay/sync-sold", params={"skus": "BK-000009", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] == 0
    assert body["duplicate_items"] >= 1
    records = _sale_records_for_sku("BK-000009")
    assert len(records) == 1


def test_sync_sold_known_sku_creates_single_sale_record_and_updates_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.LISTED)

    def fake_orders(_self):
        return [
            {
                "orderId": "ORDER-1001",
                "pricingSummary": {
                    "total": {"value": "30.00"},
                    "fee": {"value": "3.00"},
                    "deliveryCost": {"value": "2.00"},
                },
                "lineItems": [
                    {
                        "lineItemId": "LINE-1",
                        "sku": "BK-000005",
                        "lineItemCost": {"value": "30.00"},
                    }
                ],
            }
        ]

    monkeypatch.setattr("packages.ebay.src.sold_sync.SoldSync._fetch_sold_orders", fake_orders)
    stats_calls: list[tuple[str, bool, float | None]] = []

    def fake_update_stats(_self, category_id, item, sold=False, sold_price=None):
        stats_calls.append((category_id, sold, sold_price))

    monkeypatch.setattr(
        "packages.ebay.src.category_spreadsheet.CategorySpreadsheet.update_field_stats",
        fake_update_stats,
    )

    with _client() as client:
        first = client.post("/api/ebay/sync-sold", params={"skus": "BK-000005", "e2e_only": "true"})
        second = client.post("/api/ebay/sync-sold", params={"skus": "BK-000005", "e2e_only": "true"})
        sales = client.get("/api/reports/sales")

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["synced"] == 1
    assert first_body["synced_items"] == 1
    assert second_body["synced"] == 0
    assert second_body["duplicate_items"] >= 1

    records = _sale_records_for_sku("BK-000005")
    assert len(records) == 1
    assert records[0].source_report == "ebay_order:ORDER-1001|line:LINE-1|sku:BK-000005"
    assert stats_calls == [("29223", True, 30.0)]

    item = _get_item("BK-000005")
    assert item is not None
    assert item.status == ItemStatus.SOLD

    assert sales.status_code == 200
    assert any(row.get("sku") == "BK-000005" for row in sales.json())


def test_sync_sold_counts_blocked_skus_when_selected_subset(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.LISTED)
    _seed_item("BK-000008", ItemStatus.LISTED)

    def fake_orders(_self):
        return [
            {
                "orderId": "ORDER-2001",
                "pricingSummary": {"total": {"value": "12.00"}, "fee": {"value": "1.00"}},
                "lineItems": [
                    {"lineItemId": "L1", "sku": "BK-000005", "lineItemCost": {"value": "12.00"}},
                    {"lineItemId": "L2", "sku": "BK-000008", "lineItemCost": {"value": "10.00"}},
                ],
            }
        ]

    monkeypatch.setattr("packages.ebay.src.sold_sync.SoldSync._fetch_sold_orders", fake_orders)

    with _client() as client:
        resp = client.post("/api/ebay/sync-sold", params={"skus": "BK-000005", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] == 1
    assert body["blocked_skus"] == 1
    assert body["synced_items"] == 1
    assert body["skipped_items"] >= 1

    records_5 = _sale_records_for_sku("BK-000005")
    records_8 = _sale_records_for_sku("BK-000008")
    assert len(records_5) == 1
    assert len(records_8) == 0


def test_sync_sold_stats_update_failure_does_not_break_core_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item("BK-000005", ItemStatus.LISTED)

    def fake_orders(_self):
        return [
            {
                "orderId": "ORDER-STAT-FAIL",
                "pricingSummary": {"total": {"value": "20.00"}, "fee": {"value": "2.00"}},
                "lineItems": [
                    {"lineItemId": "LINE-STAT-1", "sku": "BK-000005", "lineItemCost": {"value": "20.00"}}
                ],
            }
        ]

    monkeypatch.setattr("packages.ebay.src.sold_sync.SoldSync._fetch_sold_orders", fake_orders)
    monkeypatch.setattr(
        "packages.ebay.src.category_spreadsheet.CategorySpreadsheet.update_field_stats",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stats write failed")),
    )

    with _client() as client:
        resp = client.post("/api/ebay/sync-sold", params={"skus": "BK-000005", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] == 1
    records = _sale_records_for_sku("BK-000005")
    assert len(records) == 1
