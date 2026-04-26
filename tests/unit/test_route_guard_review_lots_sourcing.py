from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import lots, review, sourcing
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.sourcing_batch import SourcingBatch
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(review.router, prefix="/api/review", tags=["review"])
    app.include_router(lots.router, prefix="/api/lots", tags=["lots"])
    app.include_router(sourcing.router, prefix="/api/sourcing", tags=["sourcing"])
    return TestClient(app)


def _configure_runtime(monkeypatch, tmp_path, *, guard_enabled: bool) -> None:
    db_path = tmp_path / "review_lots_sourcing_guard.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
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


def _seed_item(sku: str, status: str = "needs_review") -> None:
    with Session(sqlite_db.engine) as session:
        repo = ItemRepository(session)
        repo.upsert(
            Item(
                sku=sku,
                status=status,
                item_mode="single",
                title_raw=f"{sku} raw",
                title_final=f"{sku} title",
                description_final=f"{sku} desc",
                category_key="books",
                category_label="Books",
                ebay_category_id="29223",
                condition_id="5000",
                image_paths=[],
                list_price=19.99,
                cost=3.0,
            )
        )


def _seed_batch(batch_id: str = "batch-1") -> None:
    with Session(sqlite_db.engine) as session:
        session.add(
            SourcingBatch(
                batch_id=batch_id,
                label="test batch",
                total_cost=20.0,
                item_count=4,
                cost_per_item=5.0,
                sourcing_date=datetime.utcnow(),
                location="unit-test",
            )
        )
        session.commit()


def test_review_routes_allow_approved_and_block_non_approved(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)
    _seed_item("BK-000005", status="needs_review")

    routes = [
        ("POST", "/api/review/BK-000005/approve", None),
        ("POST", "/api/review/BK-000005/reject", None),
        ("PATCH", "/api/review/BK-000005/edit", {"notes": "edited"}),
    ]

    with _client() as client:
        for method, approved_path, payload in routes:
            blocked_path = approved_path.replace("BK-000005", "BK-999999")
            blocked = client.request(method, blocked_path, json=payload)
            assert blocked.status_code == 403
            assert "Only approved E2E SKUs are allowed" in blocked.json().get("detail", "")

            allowed = client.request(method, approved_path, json=payload)
            assert allowed.status_code != 403


def test_lot_routes_allow_approved_and_block_non_approved(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)
    _seed_item("BK-000005", status="approved")
    _seed_item("BK-000008", status="approved")

    with _client() as client:
        blocked = client.post(
            "/api/lots/create",
            json={"skus": ["BK-000005", "BK-999999"], "title": "Bad Lot", "price": 12.0},
        )
        assert blocked.status_code == 403
        assert "Only approved E2E SKUs are allowed" in blocked.json().get("detail", "")

        allowed = client.post(
            "/api/lots/create",
            json={"skus": ["BK-000005", "BK-000008"], "title": "Good Lot", "price": 12.0},
        )
        assert allowed.status_code == 200
        lot_sku = allowed.json()["lot_sku"]

        blocked_dissolve = client.post("/api/lots/dissolve/BK-999999")
        assert blocked_dissolve.status_code == 403
        assert "Only approved E2E SKUs are allowed" in blocked_dissolve.json().get("detail", "")

        allowed_dissolve = client.post(f"/api/lots/dissolve/{lot_sku}")
        assert allowed_dissolve.status_code != 403


def test_sourcing_routes_guard_behavior(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=True)
    _seed_item("BK-000005", status="approved")
    _seed_batch("batch-a")

    with _client() as client:
        blocked_global = client.post(
            "/api/sourcing/batch",
            json={
                "label": "guarded",
                "total_cost": 10.0,
                "item_count": 2,
                "sourcing_date": "2026-01-01T00:00:00",
                "location": "x",
            },
        )
        assert blocked_global.status_code == 403
        assert "Explicit SKU constraints are required" in blocked_global.json().get("detail", "")

        blocked_assign = client.post("/api/sourcing/assign/batch-a", json={"skus": ["BK-999999"]})
        assert blocked_assign.status_code == 403
        assert "Only approved E2E SKUs are allowed" in blocked_assign.json().get("detail", "")

        allowed_assign = client.post("/api/sourcing/assign/batch-a", json={"skus": ["BK-000005"]})
        assert allowed_assign.status_code != 403

        blocked_set_cost = client.patch("/api/sourcing/item/BK-999999", json={"cost": 5.0})
        assert blocked_set_cost.status_code == 403
        assert "Only approved E2E SKUs are allowed" in blocked_set_cost.json().get("detail", "")

        allowed_set_cost = client.patch("/api/sourcing/item/BK-000005", json={"cost": 5.0})
        assert allowed_set_cost.status_code != 403


def test_guard_off_preserves_backward_compatible_behavior(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, guard_enabled=False)
    _seed_item("BK-999999", status="needs_review")

    with _client() as client:
        review_resp = client.post("/api/review/BK-999999/reject")
        assert review_resp.status_code != 403

        create_batch_resp = client.post(
            "/api/sourcing/batch",
            json={
                "label": "legacy",
                "total_cost": 15.0,
                "item_count": 3,
                "sourcing_date": "2026-01-01T00:00:00",
                "location": "legacy",
            },
        )
        assert create_batch_resp.status_code != 403

