from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import reports
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.sale_record import SaleRecord


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "sold_readiness.db"
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


def test_sold_readiness_reports_empty_state(monkeypatch, tmp_path):
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    _configure_temp_db(monkeypatch, tmp_path)

    with _client() as client:
        resp = client.get("/api/reports/sold-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_sold_records"] == 0
    assert body["last_sold_sync_at"] is None
    assert "No sold records yet." in body["warnings"]


def test_sold_readiness_summarizes_existing_records(monkeypatch, tmp_path):
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    _configure_temp_db(monkeypatch, tmp_path)

    with Session(sqlite_db.engine) as session:
        session.add(
            SaleRecord(
                sku="BK-000005",
                platform="ebay",
                listing_id="LIST-1",
                sold_price=25.0,
                cost=5.0,
                fees=2.0,
                shipping_cost=1.0,
                gross_profit=20.0,
                net_profit=17.0,
                gross_margin=0.8,
                net_margin=0.68,
                date_sold=datetime(2026, 4, 20, 12, 0, 0),
                created_at=datetime(2026, 4, 20, 12, 5, 0),
                source_report="ebay_order:ORDER-1|line:LINE-1|sku:BK-000005",
            )
        )
        session.add(
            SaleRecord(
                sku="BK-000008",
                platform="poshmark",
                sold_price=18.0,
                cost=4.0,
                fees=1.5,
                shipping_cost=0.0,
                gross_profit=14.0,
                net_profit=12.5,
                gross_margin=0.7778,
                net_margin=0.6944,
                date_sold=datetime(2026, 4, 18, 9, 0, 0),
                created_at=datetime(2026, 4, 18, 9, 1, 0),
            )
        )
        session.commit()

    with _client() as client:
        resp = client.get("/api/reports/sold-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_sold_records"] == 2
    assert body["ebay_sold_records"] == 1
    assert body["last_sold_sync_at"] == "2026-04-20T12:05:00"
    assert body["duplicate_protection"]["enabled"] is True
    assert body["unknown_sku_count_tracked"] is False


def test_sold_readiness_surfaces_missing_auth_as_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    _configure_temp_db(monkeypatch, tmp_path)

    with _client() as client:
        resp = client.get("/api/reports/sold-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert body["sold_sync_auth"]["code"] in {"missing_token", "sandbox_production_mismatch"}
    assert "Sold sync is not ready because eBay auth needs attention." in body["warnings"]
