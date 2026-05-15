from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items, ui
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus, Platform
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items")
    app.include_router(ui.router)
    return TestClient(app)


def _configure_db(monkeypatch, tmp_path):
    db_path = tmp_path / "platform.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "false")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed(item: Item) -> None:
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(item)


def _ready_item(**overrides) -> Item:
    base = dict(
        sku="CL-PLAT",
        status=ItemStatus.APPROVED,
        category_key="clothing",
        category_label="Clothing",
        title_final="Vintage Tee",
        ebay_category_id="11450",
        ebay_category_name="Clothing",
        condition_label="Used",
        condition_id="3000",
        list_price=15.0,
        confidence_score=0.85,
        image_paths=[
            "front.jpg", "back.jpg", "brand-tag.jpg", "size-tag.jpg",
            "material-care-tag.jpg", "measurements.jpg", "flaws.jpg",
        ],
    )
    base.update(overrides)
    return Item(**base)


def test_platform_drafts_default_ebay(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_item())

    with _client() as client:
        resp = client.post("/api/items/CL-PLAT/platform-drafts", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_ebay_mutation_performed"] is True
    assert len(body["drafts"]) == 1
    assert body["drafts"][0]["platform"] == Platform.EBAY


def test_platform_drafts_can_request_multiple_platforms(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_item())

    with _client() as client:
        resp = client.post(
            "/api/items/CL-PLAT/platform-drafts",
            json={"platforms": ["ebay", "mercari", "poshmark"]},
        )

    body = resp.json()
    platforms = [d["platform"] for d in body["drafts"]]
    assert platforms == ["ebay", "mercari", "poshmark"]
    # Only eBay is implemented today:
    supported = {d["platform"]: d["platform_supported"] for d in body["drafts"]}
    assert supported["ebay"] is True
    assert supported["mercari"] is False
    assert supported["poshmark"] is False


def test_marketplace_recommendations_endpoint(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_item())

    with _client() as client:
        resp = client.post(
            "/api/items/CL-PLAT/marketplace-recommendations",
            json={"selection_mode": "hybrid"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["selection_mode"] == "hybrid"
    assert body["category_family"] == "clothing"
    assert body["no_ebay_mutation_performed"] is True
    platforms = [r["platform"] for r in body["recommendations"]]
    assert Platform.EBAY in platforms


def test_marketplace_recommendations_invalid_mode(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_item())

    with _client() as client:
        resp = client.post(
            "/api/items/CL-PLAT/marketplace-recommendations",
            json={"selection_mode": "bogus"},
        )

    assert resp.status_code == 422


def test_intake_pipeline_cockpit_renders_html(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_item())

    with _client() as client:
        resp = client.get("/intake-pipeline/CL-PLAT")

    assert resp.status_code == 200
    body = resp.text
    assert "CL-PLAT" in body
    assert "read-only" in body.lower()
    assert "platform-drafts" in body
    assert "marketplace-recommendations" in body
