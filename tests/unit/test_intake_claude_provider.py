"""Unit tests for the Claude deep-analysis provider.

All external API calls are mocked — no real Anthropic requests are made.
Tests verify:
- Default config uses deterministic fallback (no external call).
- Disabled provider returns deterministic result.
- Missing API key returns deterministic result.
- Mock success maps correctly to DeepAnalysisResult.
- Mock response with needs_more_photos blocks approval.
- Mock authenticity claim requires manual review.
- Provider output cannot set should_require_manual_review=False.
- NEVER_AUTO_OVERWRITE fields stripped from suggestions.
- Item record is NOT mutated by provider analysis.
- external_call_made flag set correctly.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import (
    DeepAnalysisRequest,
    DeterministicDeepAnalysisProvider,
    run_deep_analysis_preview,
)
from packages.intake.src.pipeline_types import ConfidenceSource, ProviderKind, RiskFlag


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _book(**kw) -> Item:
    base = dict(
        sku="BK-CLAUDE",
        category_key="books",
        category_label="Books",
        title_final="A Test Book",
        brand="Penguin",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.85,
        image_paths=[
            "front-cover.jpg", "back-cover.jpg", "spine.jpg",
            "title-page.jpg", "copyright.jpg", "condition-flaws.jpg",
        ],
    )
    base.update(kw)
    return Item(**base)


def _make_settings(**kw):
    defaults = {
        "intake_external_provider_enabled": False,
        "intake_provider": "deterministic",
        "intake_model": "",
        "enrichment_model": "claude-sonnet-4-20250514",
        "anthropic_api_key": "",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _mock_anthropic_response(content: dict) -> MagicMock:
    """Build a minimal mock for anthropic.Anthropic().messages.create()."""
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(content))]
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    return msg


def _standard_claude_response() -> dict:
    return {
        "suggested_field_updates": {"color": "Blue", "material": "Paper"},
        "confidence_by_field": {"color": 0.8, "material": 0.7},
        "evidence_by_field": {"color": ["cover is blue"], "material": ["paper texture visible"]},
        "uncertain_fields": ["size"],
        "do_not_guess_fields": ["authenticity", "first_edition", "signed_by"],
        "suggested_condition_id": "5000",
        "condition_assessment": "Good used condition, minor shelf wear.",
        "item_specifics": {"Format": "Hardcover"},
        "title_suggestions": ["Penguin Test Book Hardcover Good Condition"],
        "description_suggestion": "Hardcover in good used condition. Minor shelf wear on cover.",
        "pricing_estimate": 12.50,
        "needs_more_photos": False,
        "missing_photo_types": [],
        "publish_risk_flags": [],
        "correction_summary": ["Confirm color and material visually."],
        "should_require_manual_review": True,
        "analysis_notes": "Standard book intake.",
    }


# ── Focus: default config uses deterministic ──────────────────────────────────

def test_default_config_uses_deterministic_provider():
    """When no env override, run_deep_analysis_preview picks DeterministicDeepAnalysisProvider."""
    from packages.core.src.config import get_settings
    get_settings.cache_clear()
    item = _book()
    # Explicitly pass the deterministic provider to confirm contract.
    result = run_deep_analysis_preview(item, provider=DeterministicDeepAnalysisProvider())
    assert result.provider == "deterministic-fallback"
    assert result.is_deterministic_fallback is True
    assert result.external_call_made is False


def test_default_config_no_external_call(monkeypatch):
    """With default settings, _select_provider returns deterministic (no Claude call)."""
    from packages.core.src import config as core_config
    core_config.get_settings.cache_clear()
    monkeypatch.setenv("INTAKE_EXTERNAL_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("INTAKE_PROVIDER", "deterministic")
    core_config.get_settings.cache_clear()

    item = _book()
    result = run_deep_analysis_preview(item)
    assert result.is_deterministic_fallback is True
    assert result.external_call_made is False
    core_config.get_settings.cache_clear()


# ── Focus: provider unavailability returns fallback ───────────────────────────

def test_disabled_provider_returns_deterministic():
    """ClaudeDeepAnalysisProvider.is_available() False → caller falls to deterministic."""
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(intake_external_provider_enabled=False, intake_provider="claude")
    p = ClaudeDeepAnalysisProvider(settings)
    assert p.is_available() is False
    readiness = p.get_readiness()
    assert readiness["available"] is False
    assert readiness["code"] == "disabled"


def test_missing_api_key_makes_provider_unavailable():
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="",
    )
    p = ClaudeDeepAnalysisProvider(settings)
    assert p.is_available() is False
    assert p.get_readiness()["code"] == "missing_api_key"


def test_provider_not_selected_returns_unavailable():
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="deterministic",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)
    assert p.is_available() is False
    assert p.get_readiness()["code"] == "provider_not_selected"


# ── Focus: mock Claude success ────────────────────────────────────────────────

def test_mocked_claude_success_maps_to_deep_analysis_result():
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)

    mock_resp = _mock_anthropic_response(_standard_claude_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp

        request = DeepAnalysisRequest(
            sku="BK-CLAUDE",
            canonical_schema_version="v1",
            item=_book(),
        )
        result = p.analyze(request)

    assert result.provider == "claude-intake"
    assert result.is_deterministic_fallback is False
    assert result.provider_kind == ProviderKind.EXTERNAL_MODEL
    assert result.suggested_field_updates.get("color") == "Blue"
    assert result.suggested_field_updates.get("material") == "Paper"
    assert result.confidence_by_field.get("color") == 0.8
    assert result.pricing_estimate == 12.50
    assert result.condition_assessment == "Good used condition, minor shelf wear."
    assert result.item_specifics == {"Format": "Hardcover"}


def test_mocked_claude_sets_external_call_made_via_run_preview():
    """run_deep_analysis_preview sets external_call_made=True when real provider used."""
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    provider = ClaudeDeepAnalysisProvider(settings)
    mock_resp = _mock_anthropic_response(_standard_claude_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        result = run_deep_analysis_preview(_book(), provider=provider)

    assert result.external_call_made is True
    assert result.is_deterministic_fallback is False


# ── Focus: needs_more_photos blocks approval ──────────────────────────────────

def test_mocked_claude_needs_more_photos_blocks_approval():
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)

    resp = _standard_claude_response()
    resp["needs_more_photos"] = True
    resp["missing_photo_types"] = ["copyright/publication page", "spine"]
    mock_resp = _mock_anthropic_response(resp)

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        result = p.analyze(DeepAnalysisRequest(sku="BK-CLAUDE", canonical_schema_version="v1", item=_book()))

    assert result.needs_more_photos is True
    assert RiskFlag.MISSING_REQUIRED_PHOTOS in result.publish_risk_flags
    assert result.should_require_manual_review is True


# ── Focus: authenticity claim triggers manual review ─────────────────────────

def test_mocked_claude_authenticity_brand_requires_manual_review():
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)
    item = _book(brand="Coach", title_final="Coach bag")

    mock_resp = _mock_anthropic_response(_standard_claude_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        result = p.analyze(DeepAnalysisRequest(sku="BK-CLAUDE", canonical_schema_version="v1", item=item))

    assert RiskFlag.AUTHENTICITY_SENSITIVE_BRAND in result.authenticity_flags
    assert result.should_require_manual_review is True


# ── Focus: safety enforcement ─────────────────────────────────────────────────

def test_provider_cannot_set_should_require_manual_review_false():
    """Even if Claude returns should_require_manual_review=false, adapter forces True."""
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)

    resp = _standard_claude_response()
    resp["should_require_manual_review"] = False  # adversarial model output
    mock_resp = _mock_anthropic_response(resp)

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        result = p.analyze(DeepAnalysisRequest(sku="BK-CLAUDE", canonical_schema_version="v1", item=_book()))

    assert result.should_require_manual_review is True


def test_never_auto_overwrite_fields_stripped_from_suggestions():
    """brand, condition_id etc must not appear in suggested_field_updates."""
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)

    resp = _standard_claude_response()
    # Adversarial: try to overwrite protected fields.
    resp["suggested_field_updates"]["brand"] = "Fake Brand"
    resp["suggested_field_updates"]["condition_id"] = "3000"
    resp["suggested_field_updates"]["ebay_category_id"] = "99999"
    mock_resp = _mock_anthropic_response(resp)

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        result = p.analyze(DeepAnalysisRequest(sku="BK-CLAUDE", canonical_schema_version="v1", item=_book()))

    assert "brand" not in result.suggested_field_updates
    assert "condition_id" not in result.suggested_field_updates
    assert "ebay_category_id" not in result.suggested_field_updates
    # Safe field still present.
    assert result.suggested_field_updates.get("color") == "Blue"


def test_provider_output_does_not_mutate_item_record():
    """Calling the provider must not modify item fields."""
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)
    item = _book()
    original_brand = item.brand
    original_condition_id = item.condition_id
    original_title = item.title_final

    mock_resp = _mock_anthropic_response(_standard_claude_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        p.analyze(DeepAnalysisRequest(sku="BK-CLAUDE", canonical_schema_version="v1", item=item))

    assert item.brand == original_brand
    assert item.condition_id == original_condition_id
    assert item.title_final == original_title


# ── Focus: confidence source ──────────────────────────────────────────────────

def test_text_only_analysis_uses_mixed_confidence_source():
    """No readable local photos → confidence_source=mixed (text-only analysis)."""
    from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
    settings = _make_settings(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        anthropic_api_key="sk-test",
    )
    p = ClaudeDeepAnalysisProvider(settings)
    # image_paths are filenames only, not real local paths → no images sent.
    item = _book()  # front-cover.jpg etc. don't exist on disk

    mock_resp = _mock_anthropic_response(_standard_claude_response())

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_client_cls,
    ):
        mock_client_cls.return_value.messages.create.return_value = mock_resp
        result = p.analyze(DeepAnalysisRequest(sku="BK-CLAUDE", canonical_schema_version="v1", item=item))

    # No local images found → mixed (text-only).
    assert result.confidence_source == ConfidenceSource.MIXED
    assert result.is_deterministic_fallback is False


def test_deterministic_result_carries_no_external_call_made():
    item = _book()
    result = run_deep_analysis_preview(item, provider=DeterministicDeepAnalysisProvider())
    assert result.external_call_made is False
    assert result.is_deterministic_fallback is True
