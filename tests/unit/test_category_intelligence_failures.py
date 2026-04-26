from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items
from packages.core.src import config as core_config
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.item_record import ItemRecord
from packages.ebay.src.category_intelligence import CategoryTemplate


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "category_failures.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()

    with Session(sqlite_db.engine) as session:
        session.add(
            ItemRecord(
                sku="BK-000005",
                status="approved",
                title_final="Test Item",
                item_specifics="{}",
            )
        )
        session.commit()


def _patch_category_intelligence(monkeypatch, *, error_code: str | None = None, message: str = "failure"):
    monkeypatch.setattr(
        "packages.ebay.src.category_intelligence.CategoryIntelligence.get_category_id",
        lambda self, _item: ("29223", "Books"),
    )
    monkeypatch.setattr(
        "packages.ebay.src.category_intelligence.CategoryIntelligence.get_template",
        lambda self, _cat_id: Result.failure(message, error_code=error_code),
    )


def test_category_intelligence_timeout_returns_deterministic_json(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _patch_category_intelligence(
        monkeypatch, error_code="UPSTREAM_TIMEOUT", message="template_fetch_error: timeout"
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/category-intelligence")

    assert resp.status_code == 504
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "UPSTREAM_TIMEOUT"
    assert "timeout" in detail.get("message", "").lower()


def test_category_intelligence_connection_failure_returns_deterministic_json(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _patch_category_intelligence(
        monkeypatch, error_code="UPSTREAM_CONNECTION", message="template_fetch_error: connect failed"
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/category-intelligence")

    assert resp.status_code == 502
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "UPSTREAM_CONNECTION"


def test_category_intelligence_auth_failure_returns_deterministic_json(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _patch_category_intelligence(
        monkeypatch, error_code="AUTH_FAILED", message="eBay API 401 unauthorized"
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/category-intelligence")

    assert resp.status_code == 502
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "AUTH_FAILED"


def test_category_intelligence_malformed_response_returns_deterministic_json(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _patch_category_intelligence(
        monkeypatch, error_code="MALFORMED_RESPONSE", message="template_malformed_response"
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/category-intelligence")

    assert resp.status_code == 502
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "MALFORMED_RESPONSE"


def test_category_intelligence_success_still_works(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "packages.ebay.src.category_intelligence.CategoryIntelligence.get_category_id",
        lambda self, _item: ("29223", "Books"),
    )
    monkeypatch.setattr(
        "packages.ebay.src.category_intelligence.CategoryIntelligence.get_template",
        lambda self, _cat_id: Result.success(
            CategoryTemplate(
                category_id="29223",
                category_name="Books",
                required_fields=[],
                recommended_fields=[],
                field_constraints={},
                fetched_at=datetime.utcnow(),
                raw_response={"aspects": []},
            )
        ),
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/category-intelligence")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "BK-000005"
    assert body["publish_ready"] is True
