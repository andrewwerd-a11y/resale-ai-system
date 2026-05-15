from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from apps.api.src.routes import items, review
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items")
    app.include_router(review.router, prefix="/api/review")
    return TestClient(app)


def _configure_db(monkeypatch, tmp_path):
    db_path = tmp_path / "intake_quality.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "false")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed(item: Item):
    from sqlmodel import Session

    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(item)


def test_intake_quality_endpoint_and_correction_report_are_read_only(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(
        Item(
            sku="BK-QUALITY",
            status=ItemStatus.PENDING_INTAKE,
            category_key="books",
            category_label="Books",
            title_final="Reference Book",
            condition_label="Good",
            condition_id="5000",
            image_paths=["front-cover.jpg", "back-cover.jpg", "spine.jpg", "title-page.jpg", "condition-flaws.jpg"],
        )
    )

    with _client() as client:
        quality_resp = client.get("/api/items/BK-QUALITY/intake-quality")
        report_resp = client.get("/api/items/BK-QUALITY/correction-report")

    assert quality_resp.status_code == 200
    quality = quality_resp.json()
    assert quality["intake_quality_status"] == "NEEDS_MORE_PHOTOS"
    assert quality["needs_more_photos_for_analysis"] is True
    assert "copyright/publication page" in quality["missing_photo_types"]

    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["no_ebay_mutation_performed"] is True
    assert report["missing_photo_checklist"] == quality["missing_photo_types"]
    assert report["next_action_sequence"][0]["group"] == "Add more photos before analysis"


def test_analyze_blocks_before_provider_when_intake_quality_not_ready(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(
        Item(
            sku="BK-BLOCK",
            status=ItemStatus.PENDING_INTAKE,
            photo_folder=str(tmp_path),
            category_key="books",
            category_label="Books",
            title_final="Book",
            condition_label="Good",
            condition_id="5000",
            image_paths=["front-cover.jpg"],
        )
    )

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed when quality gate blocks")

    monkeypatch.setattr("packages.vision.src.ollama_provider.OllamaProvider", fail_provider)

    with _client() as client:
        resp = client.post("/api/items/BK-BLOCK/analyze")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "intake_quality_blocked"
    assert detail["intake_quality"]["should_run_deep_analysis"] is False


def test_review_approval_blocks_when_quality_not_ready(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(
        Item(
            sku="CL-BLOCK",
            status=ItemStatus.NEEDS_REVIEW,
            category_key="clothing",
            category_label="Clothing",
            title_final="Jacket",
            condition_label="Good",
            condition_id="3000",
            image_paths=["front.jpg", "back.jpg"],
            needs_review=True,
        )
    )

    with _client() as client:
        resp = client.post("/api/review/CL-BLOCK/approve")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "intake_quality_blocked"
    assert "size tag" in detail["intake_quality"]["missing_photo_types"]


def test_bulk_approve_reports_blocked_items_without_approving(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(
        Item(
            sku="CL-BULK",
            status=ItemStatus.NEEDS_REVIEW,
            category_key="clothing",
            category_label="Clothing",
            title_final="Jacket",
            condition_label="Good",
            condition_id="3000",
            image_paths=["front.jpg"],
            needs_review=True,
        )
    )

    with _client() as client:
        resp = client.post("/api/items/bulk-approve", json={"skus": ["CL-BULK"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 0
    assert body["blocked"][0]["code"] == "intake_quality_blocked"
