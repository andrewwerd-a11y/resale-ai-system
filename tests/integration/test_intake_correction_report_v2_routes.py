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
    db_path = tmp_path / "correction_v2.db"
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
        sku="BK-V2",
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


def test_correction_report_v2_includes_all_sections(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "v2"
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_external_provider_called"] is True
    assert body["first_pass_identity"]["decision"] == "READY_FOR_DEEP_ANALYSIS"
    assert body["category_candidates"]["marketplace_candidates"]
    assert body["marketplace_requirements"]["platform"] == "ebay"
    assert body["deep_analysis_preview"] is not None
    assert "grouped_next_actions" in body
    assert body["extraction_confidence"] == 0.85
    assert body["category_confidence"] != body["extraction_confidence"]
    assert body["photo_evidence_confidence"] is not None
    assert body["confidence_explanation"]
    assert "Category has not been operator-confirmed." in body["confidence_warnings"]


def test_correction_report_v2_groups_actions_for_missing_photos(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book(image_paths=["front-cover.jpg"]))

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2")

    body = resp.json()
    groups = {entry["group"] for entry in body["grouped_next_actions"]}
    assert "Needs more photos" in groups
    assert body["should_run_deep_analysis"] is False
    assert body["publish_approval_blocked"] is True
    evidence = body["operator_photo_evidence"]
    assert evidence["intake_quality_status"] == "LOW_CONFIDENCE_HOLD"
    assert evidence["needs_more_photos_for_analysis"] is True
    assert evidence["missing_required_photo_types"] == [
        "back cover",
        "spine",
        "condition/flaws",
        "title page",
        "copyright/publication page",
    ]
    assert evidence["missing_photo_types"] == evidence["missing_required_photo_types"]
    assert evidence["missing_recommended_photo_types"] == []
    assert evidence["selected_photo_types"] == []
    assert evidence["selected_image_count"] is None
    assert evidence["skipped_image_count"] is None
    assert evidence["skipped_image_reasons"] == []
    assert evidence["deep_analysis_image_selection_available"] is False
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_publish_performed"] is True
    assert body["manual_approval_required"] is True


def test_correction_report_v2_allows_limited_evidence_draft_annotations(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book(image_paths=["front-cover.jpg"]))

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2?allow_limited_evidence=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["limited_evidence_mode"] is True
    assert body["limited_evidence_used"] is True
    assert body["draft_quality"] == "incomplete"
    assert body["confidence_source"] == "limited_evidence"
    assert body["deep_analysis_preview"] is not None
    assert body["deep_analysis_preview"]["confidence_source"] == "limited_evidence"
    assert body["operator_warning"].startswith("This draft was generated with incomplete evidence.")
    assert "Draft was generated with incomplete evidence." in body["confidence_warnings"]
    assert body["publish_approval_blocked"] is True
    assert body["manual_approval_required"] is True
    assert body["missing_required_photo_types"]
    assert body["missing_recommended_photo_types"] == []


def test_correction_report_v2_uses_stored_photo_labels(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(
        _ready_book(
            image_paths=["img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg", "img5.jpg"],
        )
    )

    with _client() as client:
        before = client.get("/api/items/BK-V2/correction-report-v2")
        patch = client.patch(
            "/api/items/BK-V2/photos/metadata",
            json={
                "updates": [
                    {"image_path": "img1.jpg", "photo_type": "front"},
                    {"image_path": "img2.jpg", "photo_type": "back"},
                    {"image_path": "img3.jpg", "photo_type": "spine"},
                    {"image_path": "img4.jpg", "photo_type": "title_page"},
                    {"image_path": "img5.jpg", "photo_type": "copyright_page"},
                ]
            },
        )
        after = client.get("/api/items/BK-V2/correction-report-v2")

    assert before.status_code == 200
    assert patch.status_code == 200
    assert after.status_code == 200
    before_missing = set(before.json()["operator_photo_evidence"]["missing_photo_types"])
    after_missing = set(after.json()["operator_photo_evidence"]["missing_photo_types"])
    assert "spine" in before_missing
    assert "title page" in before_missing
    assert "copyright/publication page" in before_missing
    assert "spine" not in after_missing
    assert "title page" not in after_missing
    assert "copyright/publication page" not in after_missing


def test_correction_report_v2_flags_malformed_condition_id(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book(condition_id="[3000, 4000]"))

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2")

    body = resp.json()
    assert any("malformed" in entry.lower() for entry in body["malformed_data"])


def test_correction_report_v2_compatibility_blocker_blocks_top_level_gates(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    from apps.api.src.services import intake_correction_report_v2 as report_v2

    class _Ready:
        def as_dict(self):
            return {"ready": True, "blockers": [], "required_actions": []}

    monkeypatch.setattr(report_v2, "evaluate_publish_readiness", lambda item: _Ready())
    monkeypatch.setattr(
        report_v2,
        "evaluate_publish_compatibility",
        lambda item, strict_condition_policy=True: {
            "ready": False,
            "blockers": ["Condition policy for the selected category is not cached locally."],
            "required_actions": [
                "Fetch or confirm category-specific condition compatibility before retrying publish."
            ],
        },
    )

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2")

    body = resp.json()
    assert body["publish_readiness"]["ready"] is True
    assert body["publish_compatibility"]["ready"] is False
    assert body["platform_translation_allowed"] is False
    assert body["publish_approval_blocked"] is True


def test_reanalysis_preview_brand_change_triggers_rerun(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.post(
            "/api/items/BK-V2/reanalysis-preview",
            json={"pending_updates": {"brand": "Coach"}},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["impact_summary"]["should_rerun_deep_analysis"] is True
    assert body["impact_summary"]["affects_identity"] is True
    # Brand edits are flagged as risky claim:
    assert body["trust_assessments"][0]["trust_level"] == "risky_claim"


def test_reanalysis_preview_ignores_no_op_edits(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book(brand="Penguin"))

    with _client() as client:
        resp = client.post(
            "/api/items/BK-V2/reanalysis-preview",
            json={"pending_updates": {"brand": "Penguin"}},
        )

    body = resp.json()
    assert body["pending_change_events"] == []
    assert body["impact_summary"]["should_rerun_deep_analysis"] is False


def test_reanalysis_preview_color_edit_does_not_force_rerun(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.post(
            "/api/items/BK-V2/reanalysis-preview",
            json={"pending_updates": {"color": "Blue"}},
        )

    body = resp.json()
    # color is a factual observation: no rerun required
    assert body["impact_summary"]["should_rerun_deep_analysis"] is False


def test_correction_report_v2_404_for_unknown(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    with _client() as client:
        resp = client.get("/api/items/UNK/correction-report-v2")
    assert resp.status_code == 404


# ── no_external_provider_called accuracy ──────────────────────────────────────

def test_correction_report_v2_no_external_provider_called_true_when_deterministic(
    monkeypatch, tmp_path
):
    """Deterministic deep result → no_external_provider_called=True."""
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2")

    assert resp.status_code == 200
    assert resp.json()["no_external_provider_called"] is True


def test_correction_report_v2_no_external_provider_called_false_when_real_provider_used(
    monkeypatch, tmp_path
):
    """When deep.external_call_made=True, no_external_provider_called must be False."""
    from apps.api.src.services import intake_correction_report_v2 as report_v2
    from packages.intake.src.analysis_contract import DeepAnalysisResult
    from packages.intake.src.pipeline_types import ConfidenceSource, ProviderKind

    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    fake_deep = DeepAnalysisResult(
        sku="BK-V2",
        provider="claude-intake",
        provider_kind=ProviderKind.EXTERNAL_MODEL,
        confidence_source=ConfidenceSource.MIXED,
        is_deterministic_fallback=False,
        external_call_made=True,
        fallback_warning="",
        should_require_manual_review=True,
        should_block_publish_approval=True,
    )

    monkeypatch.setattr(report_v2, "run_deep_analysis_preview", lambda *a, **kw: fake_deep)

    with _client() as client:
        resp = client.get("/api/items/BK-V2/correction-report-v2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_external_provider_called"] is False
    # Safety gates must remain unchanged regardless of provider.
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_publish_performed"] is True
    assert body["manual_approval_required"] is True
    assert body["read_only"] is True
    evidence = body["operator_photo_evidence"]
    assert evidence["intake_quality_status"] == "READY_FOR_DEEP_ANALYSIS"
    assert evidence["needs_more_photos_for_analysis"] is False
    assert evidence["missing_photo_types"] == []
    assert evidence["missing_required_photo_types"] == []
    assert evidence["missing_recommended_photo_types"] == []
    assert evidence["selected_photo_types"] == []
    assert evidence["selected_image_count"] == 0
    assert evidence["skipped_image_count"] == 0
    assert evidence["skipped_image_reasons"] == []
    assert evidence["deep_analysis_image_selection_available"] is True
