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
    db_path = tmp_path / "intake_pipeline.db"
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


def _ready_book(**overrides) -> Item:
    base = dict(
        sku="BK-PIPE",
        status=ItemStatus.PENDING_INTAKE,
        category_key="books",
        category_label="Books",
        title_final="Reference Book",
        brand="Penguin",
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


def _limited_book(**overrides) -> Item:
    base = dict(
        sku="BK-LIMIT",
        status=ItemStatus.PENDING_INTAKE,
        category_key="books",
        category_label="Books",
        title_final="Thin Evidence Book",
        brand="Penguin",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.55,
        image_paths=["front-cover.jpg"],
    )
    base.update(overrides)
    return Item(**base)


def test_intake_pipeline_status_returns_all_stages(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.get("/api/items/BK-PIPE/intake-pipeline-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_external_provider_called"] is True
    assert body["extraction_confidence"] == 0.85
    assert body["extraction_confidence_source"] == "phase1_vision_model_self_report"
    assert body["category_confidence"] != body["extraction_confidence"]
    assert body["category_confidence_source"]
    assert body["photo_evidence_confidence"] is not None
    assert body["confidence_explanation"]
    assert "Category has not been operator-confirmed." in body["confidence_warnings"]
    stages = body["stages"]
    assert stages["PHOTO_INTAKE"]["total_photos"] == 6
    assert stages["FIRST_PASS_IDENTITY"]["decision"] == "READY_FOR_DEEP_ANALYSIS"
    assert stages["DEEP_ANALYSIS"] is None  # not requested


def test_intake_pipeline_status_can_run_deep_analysis_preview(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.get("/api/items/BK-PIPE/intake-pipeline-status?run_deep_analysis=true")

    assert resp.status_code == 200
    deep = resp.json()["stages"]["DEEP_ANALYSIS"]
    assert deep is not None
    assert deep["provider"] == "deterministic-fallback"
    assert deep["should_require_manual_review"] is True


def test_identity_scan_endpoint(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.post("/api/items/BK-PIPE/identity-scan", json={"user_context": "donated book"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "READY_FOR_DEEP_ANALYSIS"
    assert body["no_ebay_mutation_performed"] is True


def test_category_candidates_endpoint(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book(ebay_category_id="11450", category_template_fetched=True))

    with _client() as client:
        resp = client.post("/api/items/BK-PIPE/category-candidates")

    assert resp.status_code == 200
    body = resp.json()
    assert any(c["category_id"] == "11450" for c in body["marketplace_candidates"])


def test_marketplace_requirements_endpoint_unfetched_flags_live(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book(category_template_fetched=False))

    with _client() as client:
        resp = client.get("/api/items/BK-PIPE/marketplace-requirements?platform=ebay")

    assert resp.status_code == 200
    body = resp.json()
    assert body["requires_live_read_only_fetch"] is True
    assert body["category_policy_source"] == "deterministic-fallback"


def test_deep_analysis_preview_endpoint_never_publishes(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.post("/api/items/BK-PIPE/deep-analysis-preview", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_external_provider_called"] is True
    assert body["provider"] == "deterministic-fallback"
    assert body["should_block_publish_approval"] is True
    assert body["publish_risk_flags"]


def test_deep_analysis_preview_blocks_without_limited_evidence_flag(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_limited_book())

    with _client() as client:
        resp = client.post("/api/items/BK-LIMIT/deep-analysis-preview", json={})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "intake_quality_blocked"
    assert detail["limited_evidence_draft_available"] is True
    assert "required evidence" in detail["message"].lower()


def test_deep_analysis_preview_allows_limited_evidence_draft_with_warning(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_limited_book())

    with _client() as client:
        resp = client.post(
            "/api/items/BK-LIMIT/deep-analysis-preview",
            json={"allow_limited_evidence": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["limited_evidence_mode"] is True
    assert body["limited_evidence_used"] is True
    assert body["draft_quality"] == "incomplete"
    assert body["confidence_source"] == "limited_evidence"
    assert body["missing_required_photo_types"]
    assert body["operator_warning"].startswith("This draft was generated with incomplete evidence.")
    assert "Draft was generated with incomplete evidence." in body["confidence_warnings"]
    assert body["publish_approval_blocked"] is True
    assert body["manual_approval_required"] is True
    assert body["no_publish_performed"] is True
    assert body["no_ebay_mutation_performed"] is True
    assert body["should_block_publish_approval"] is True


def test_intake_pipeline_status_can_run_limited_evidence_preview(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_limited_book())

    with _client() as client:
        resp = client.get(
            "/api/items/BK-LIMIT/intake-pipeline-status?run_deep_analysis=true&allow_limited_evidence=true"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["limited_evidence_mode"] is True
    assert body["limited_evidence_used"] is True
    assert body["draft_quality"] == "incomplete"
    assert body["publish_approval_blocked"] is True
    assert body["stages"]["DEEP_ANALYSIS"] is not None
    assert body["stages"]["DEEP_ANALYSIS"]["confidence_source"] == "limited_evidence"


def test_intake_pipeline_warns_when_high_extraction_confidence_has_low_photo_evidence(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_limited_book(confidence_score=0.9))

    with _client() as client:
        resp = client.get("/api/items/BK-LIMIT/intake-pipeline-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_confidence"] == 0.9
    assert body["photo_evidence_confidence"] < 0.55
    assert (
        "High extraction confidence does not mean category or publish readiness is confirmed."
        in body["confidence_warnings"]
    )


def test_404_for_unknown_sku(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)

    with _client() as client:
        resp = client.get("/api/items/UNKNOWN/intake-pipeline-status")

    assert resp.status_code == 404
