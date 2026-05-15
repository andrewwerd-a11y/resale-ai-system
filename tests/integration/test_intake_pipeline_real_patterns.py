"""Regression tests for the real-pattern issues observed in the latest
31-SKU bulk-publish preview:

  - malformed condition_id (list-like or explanatory string)
  - blank condition_id
  - missing required aspects
  - category policy unknown
  - local-only photo blocker
  - mixed photo state (some hosted, some local)
  - existing unpublished offer state
  - needs_review status blocker

These exercise the staged-intake pipeline end-to-end and assert it produces
the right next-action group and never marks a SKU publish_allowed=True.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items")
    return TestClient(app)


def _configure_db(monkeypatch, tmp_path):
    db_path = tmp_path / "patterns.db"
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


def _book(**overrides) -> Item:
    base = dict(
        sku="BK-REG",
        status=ItemStatus.PENDING_INTAKE,
        category_key="books",
        category_label="Books",
        title_final="A Book",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.85,
        image_paths=[
            "front-cover.jpg", "back-cover.jpg", "spine.jpg",
            "title-page.jpg", "copyright.jpg", "condition-flaws.jpg",
        ],
    )
    base.update(overrides)
    return Item(**base)


def test_malformed_condition_id_list_like_string(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-MAL1", condition_id="[3000, 4000]"))

    with _client() as client:
        resp = client.get("/api/items/BK-MAL1/correction-report-v2")

    body = resp.json()
    assert any("malformed" in entry.lower() for entry in body["malformed_data"])
    assert body["publish_approval_blocked"] is True


def test_malformed_condition_id_explanatory_string(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-MAL2", condition_id="see notes"))

    with _client() as client:
        resp = client.get("/api/items/BK-MAL2/correction-report-v2")

    body = resp.json()
    assert any("malformed" in entry.lower() for entry in body["malformed_data"])


def test_blank_condition_does_not_produce_malformed_flag(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-BLANK", condition_id=""))

    with _client() as client:
        resp = client.get("/api/items/BK-BLANK/correction-report-v2")

    body = resp.json()
    # blank is missing, not malformed
    assert body["malformed_data"] == []
    # publish should still be blocked overall.
    assert body["publish_approval_blocked"] is True


def test_missing_required_aspects_surface_in_grouped_actions(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-ASP", missing_required_fields=["Brand", "Format"]))

    with _client() as client:
        resp = client.get("/api/items/BK-ASP/correction-report-v2")

    body = resp.json()
    assert "Brand" in body["marketplace_requirements"]["required_aspects"]


def test_category_policy_unknown_groups_action(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-POL", category_template_fetched=False))

    with _client() as client:
        resp = client.get("/api/items/BK-POL/correction-report-v2")

    body = resp.json()
    groups = {entry["group"] for entry in body["grouped_next_actions"]}
    assert "Fetch category policy" in groups


def test_local_only_photos_block_platform_translation(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    # local-only image_paths and needed_review status — should not be publish_allowed
    _seed(_book(sku="BK-LOC", status=ItemStatus.NEEDS_REVIEW, needs_review=True))

    with _client() as client:
        resp = client.post("/api/items/BK-LOC/platform-drafts", json={})

    draft = resp.json()["drafts"][0]
    assert draft["publish_allowed"] is False


def test_mixed_photo_state_remains_blocked(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(
        sku="BK-MIX",
        image_paths=["front-cover.jpg", "https://example.cdn/back.jpg"],
        status=ItemStatus.NEEDS_REVIEW,
        needs_review=True,
    ))

    with _client() as client:
        resp = client.post("/api/items/BK-MIX/platform-drafts", json={})

    draft = resp.json()["drafts"][0]
    assert draft["publish_allowed"] is False


def test_existing_unpublished_offer_state_blocks_translation(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(
        sku="BK-OFFER",
        status=ItemStatus.APPROVED,
        offer_id="OFR-123",
    ))

    with _client() as client:
        resp = client.post("/api/items/BK-OFFER/platform-drafts", json={})

    draft = resp.json()["drafts"][0]
    # Approved-with-existing-offer should not auto-publish through translator
    assert draft["manual_review_required"] is True


def test_needs_review_status_blocker(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-NR", status=ItemStatus.NEEDS_REVIEW, needs_review=True))

    with _client() as client:
        resp = client.get("/api/items/BK-NR/correction-report-v2")

    body = resp.json()
    assert body["publish_approval_blocked"] is True


def test_high_value_item_requires_manual_review_through_pipeline(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_book(sku="BK-HV", estimated_price=250.0))

    with _client() as client:
        resp = client.get("/api/items/BK-HV/correction-report-v2")

    body = resp.json()
    assert body["human_review_required"] is True
