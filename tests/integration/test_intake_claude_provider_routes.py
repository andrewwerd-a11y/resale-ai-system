"""Integration tests for the deep-analysis-preview endpoint with provider flags.

Verifies:
- Default config → external_provider_disabled=True, no_external_provider_called=True.
- Provider disabled flag is in response.
- Safety flags (read_only, draft_only, manual_approval_required) always present.
- Mock Claude success → no_external_provider_called=False, external_provider_disabled=False.
- Full publish safety tests are unaffected.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from packages.core.src.constants import ItemStatus


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items")
    return TestClient(app)


def _configure_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test_claude_routes.db"
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
        sku="BK-PROV",
        status=ItemStatus.PENDING_INTAKE,
        category_key="books",
        category_label="Books",
        title_final="Test Book",
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


def _mock_claude_response(content: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(content))]
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    return msg


def _standard_response() -> dict:
    return {
        "suggested_field_updates": {"color": "Green"},
        "confidence_by_field": {"color": 0.75},
        "evidence_by_field": {"color": ["cover is green"]},
        "uncertain_fields": ["material"],
        "do_not_guess_fields": ["authenticity", "first_edition", "signed_by"],
        "suggested_condition_id": "5000",
        "condition_assessment": "Good condition.",
        "item_specifics": {},
        "title_suggestions": [],
        "description_suggestion": "Good used book.",
        "pricing_estimate": 10.0,
        "needs_more_photos": False,
        "missing_photo_types": [],
        "publish_risk_flags": [],
        "correction_summary": [],
        "should_require_manual_review": True,
        "analysis_notes": "",
    }


# ── Default config (provider disabled) ────────────────────────────────────────

def test_deep_analysis_preview_default_is_deterministic(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())
    monkeypatch.setenv("INTAKE_EXTERNAL_PROVIDER_ENABLED", "false")
    core_config.get_settings.cache_clear()

    with _client() as client:
        resp = client.post("/api/items/BK-PROV/deep-analysis-preview", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_external_provider_called"] is True
    assert body["external_provider_disabled"] is True
    assert body["read_only"] is True
    assert body["draft_only"] is True
    assert body["manual_approval_required"] is True
    assert body["provider"] == "deterministic-fallback"
    core_config.get_settings.cache_clear()


def test_deep_analysis_preview_always_has_safety_flags(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())

    with _client() as client:
        resp = client.post("/api/items/BK-PROV/deep-analysis-preview", json={})

    assert resp.status_code == 200
    body = resp.json()
    for flag in ("no_ebay_mutation_performed", "no_publish_performed",
                 "read_only", "draft_only", "manual_approval_required"):
        assert body[flag] is True, f"Expected {flag}=True"


def test_deep_analysis_preview_configured_provider_in_response(monkeypatch, tmp_path):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())
    monkeypatch.setenv("INTAKE_PROVIDER", "deterministic")
    core_config.get_settings.cache_clear()

    with _client() as client:
        resp = client.post("/api/items/BK-PROV/deep-analysis-preview", json={})

    assert resp.status_code == 200
    assert resp.json()["configured_provider"] == "deterministic"
    core_config.get_settings.cache_clear()


# ── Mocked Claude success ──────────────────────────────────────────────────────

def test_deep_analysis_preview_mocked_claude_sets_no_external_provider_called_false(
    monkeypatch, tmp_path
):
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())
    monkeypatch.setenv("INTAKE_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("INTAKE_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-mock")
    core_config.get_settings.cache_clear()

    mock_resp = _mock_claude_response(_standard_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_cls,
        _client() as client,
    ):
        mock_cls.return_value.messages.create.return_value = mock_resp
        resp = client.post("/api/items/BK-PROV/deep-analysis-preview", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_external_provider_called"] is False
    assert body["external_provider_disabled"] is False
    assert body["provider"] == "claude-intake"
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_publish_performed"] is True
    assert body["should_require_manual_review"] is True
    core_config.get_settings.cache_clear()


def test_deep_analysis_preview_mocked_claude_cannot_publish(monkeypatch, tmp_path):
    """Even with real provider, publish gates remain blocked."""
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())
    monkeypatch.setenv("INTAKE_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("INTAKE_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-mock")
    core_config.get_settings.cache_clear()

    mock_resp = _mock_claude_response(_standard_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_cls,
        _client() as client,
    ):
        mock_cls.return_value.messages.create.return_value = mock_resp
        resp = client.post("/api/items/BK-PROV/deep-analysis-preview", json={})

    body = resp.json()
    assert body["no_publish_performed"] is True
    assert body["manual_approval_required"] is True
    # should_require_manual_review is always True from adapter.
    assert body["should_require_manual_review"] is True
    core_config.get_settings.cache_clear()


def test_deep_analysis_preview_missing_key_falls_back_to_deterministic(monkeypatch, tmp_path):
    """If INTAKE_EXTERNAL_PROVIDER_ENABLED=true but key missing → deterministic fallback."""
    _configure_db(monkeypatch, tmp_path)
    _seed(_ready_book())
    monkeypatch.setenv("INTAKE_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("INTAKE_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    core_config.get_settings.cache_clear()

    with _client() as client:
        resp = client.post("/api/items/BK-PROV/deep-analysis-preview", json={})

    assert resp.status_code == 200
    body = resp.json()
    # Falls back to deterministic — no external call.
    assert body["provider"] == "deterministic-fallback"
    assert body["no_external_provider_called"] is True
    core_config.get_settings.cache_clear()
