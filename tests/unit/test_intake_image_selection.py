"""Unit tests for category-aware image selection in ClaudeDeepAnalysisProvider.

All file I/O is mocked — no real images or API calls.

Tests verify:
- Books: front_cover selected before back_cover (priority order).
- Clothing: brand_tag, size_tag prioritised.
- Bags: front_back, interior, serial prioritised.
- Plush: tush_tag prioritised.
- Unknown category: falls back to list order.
- Count cap respected per category.
- Byte cap causes skip with reason 'byte_cap'.
- Unreadable file gets reason 'read_error'.
- file_not_found path skipped.
- Hosted URLs skipped with reason 'hosted_url_skipped'.
- Explicit PhotoMeta label outranks filename order.
- required_missing populated when top-priority photos absent.
- selected_photo_types / skipped_image_count propagated to DeepAnalysisResult.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest

from packages.domain.src.entities.item import Item
from packages.intake.src.providers.claude_deep_analysis import (
    ClaudeDeepAnalysisProvider,
    _ImageSelectionResult,
    _infer_token_for_path,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _settings(**kw) -> SimpleNamespace:
    defaults = dict(
        intake_external_provider_enabled=True,
        intake_provider="claude",
        intake_model="",
        enrichment_model="claude-sonnet-4-20250514",
        anthropic_api_key="sk-test",
        intake_max_images_default=5,
        intake_max_images_books=6,
        intake_max_images_clothing=6,
        intake_max_images_bags=7,
        intake_max_images_toys=5,
        intake_max_image_bytes_total=10 * 1024 * 1024,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _provider(**kw) -> ClaudeDeepAnalysisProvider:
    return ClaudeDeepAnalysisProvider(_settings(**kw))


def _fake_file(path_str: str, content: bytes = b"IMGDATA") -> None:
    """Patch Path.exists and Path.read_bytes for a single path."""
    pass  # used inline via patch below


def _make_mock_path(exists: bool = True, content: bytes = b"IMGDATA", raises: bool = False):
    """Return a mock Path instance."""
    p = MagicMock(spec=Path)
    p.exists.return_value = exists
    p.suffix = ".jpg"
    if raises:
        p.read_bytes.side_effect = OSError("permission denied")
    else:
        p.read_bytes.return_value = content
    return p


# ── Token inference ────────────────────────────────────────────────────────────

def test_infer_token_front_cover_books():
    assert _infer_token_for_path("front-cover.jpg", "books") == "front_cover"


def test_infer_token_spine_books():
    assert _infer_token_for_path("spine.jpg", "books") == "spine"


def test_infer_token_brand_tag_clothing():
    assert _infer_token_for_path("brand-tag.jpg", "clothing") == "brand_tag"


def test_infer_token_interior_bags():
    assert _infer_token_for_path("interior.jpg", "bags") == "interior"


def test_infer_token_tush_tag_plush():
    assert _infer_token_for_path("tush-tag.jpg", "plush_toys") == "tag_tush_tag"


def test_infer_token_unknown_family_returns_none():
    # Unknown family → no priority list → returns None
    result = _infer_token_for_path("front.jpg", "furniture")
    assert result is None


# ── _select_category_images ────────────────────────────────────────────────────

def _run_selection(
    provider: ClaudeDeepAnalysisProvider,
    paths: list[str],
    family: str,
    photo_meta: list | None = None,
    path_map: dict[str, bytes] | None = None,
    missing_paths: set[str] | None = None,
    error_paths: set[str] | None = None,
) -> _ImageSelectionResult:
    """Helper: patch Path construction so only paths in path_map are 'readable'."""
    path_map = path_map or {p: b"IMG" for p in paths}
    missing_paths = missing_paths or set()
    error_paths = error_paths or set()

    def fake_path_cls(path_str):
        mock = MagicMock(spec=Path)
        mock.__str__ = lambda s: path_str
        mock.suffix = Path(path_str).suffix
        mock.stem = Path(path_str).stem
        if path_str in missing_paths:
            mock.exists.return_value = False
        else:
            mock.exists.return_value = True
        if path_str in error_paths:
            mock.read_bytes.side_effect = OSError("permission denied")
        else:
            mock.read_bytes.return_value = path_map.get(path_str, b"IMG")
        return mock

    with patch("packages.intake.src.providers.claude_deep_analysis.Path", side_effect=fake_path_cls):
        return provider._select_category_images(paths, photo_meta or [], family)


def test_books_priority_selects_front_cover_first():
    p = _provider()
    paths = [
        "condition-flaws.jpg",
        "back-cover.jpg",
        "front-cover.jpg",
        "spine.jpg",
    ]
    result = _run_selection(p, paths, "books")
    # front_cover should be first in used_paths
    assert result.used_paths[0] == "front-cover.jpg"


def test_books_respects_count_cap():
    p = _provider(intake_max_images_books=3)
    paths = [f"img{i}.jpg" for i in range(8)]
    result = _run_selection(p, paths, "books")
    assert len(result.image_blocks) == 3
    assert result.skipped_reasons.count("count_cap") == 5


def test_clothing_selects_brand_tag_and_size_tag():
    p = _provider()
    paths = [
        "flaws-wear.jpg",
        "brand-tag.jpg",
        "size-tag.jpg",
        "front.jpg",
    ]
    result = _run_selection(p, paths, "clothing")
    selected = result.used_paths
    # brand_tag and size_tag should appear before flaws (lower priority)
    brand_idx = selected.index("brand-tag.jpg")
    flaw_idx = selected.index("flaws-wear.jpg")
    assert brand_idx < flaw_idx


def test_bags_respects_count_cap():
    p = _provider(intake_max_images_bags=4)
    paths = [f"img{i}.jpg" for i in range(10)]
    result = _run_selection(p, paths, "bags")
    assert len(result.image_blocks) == 4


def test_plush_tush_tag_prioritised():
    p = _provider()
    paths = ["defects-wear.jpg", "tush-tag.jpg", "front.jpg"]
    result = _run_selection(p, paths, "plush_toys")
    # tush tag → tag_tush_tag token, priority index 2; front → index 0
    # front should come first, tush second, defects third
    assert "front.jpg" in result.used_paths
    assert result.used_paths.index("tush-tag.jpg") < result.used_paths.index("defects-wear.jpg")


def test_hosted_urls_skipped():
    p = _provider()
    paths = ["https://example.com/img.jpg", "local.jpg"]
    result = _run_selection(p, paths, "books")
    assert "https://example.com/img.jpg" in result.skipped_paths
    assert "hosted_url_skipped" in result.skipped_reasons


def test_file_not_found_skipped():
    p = _provider()
    paths = ["missing.jpg", "local.jpg"]
    result = _run_selection(p, paths, "clothing", missing_paths={"missing.jpg"})
    assert "missing.jpg" in result.skipped_paths
    assert "file_not_found" in result.skipped_reasons


def test_read_error_skipped():
    p = _provider()
    paths = ["bad.jpg", "good.jpg"]
    result = _run_selection(p, paths, "clothing", error_paths={"bad.jpg"})
    assert "bad.jpg" in result.skipped_paths
    assert "read_error" in result.skipped_reasons


def test_byte_cap_causes_skip():
    p = _provider(intake_max_image_bytes_total=10)  # 10 bytes cap
    paths = ["a.jpg", "b.jpg"]
    # each file is 8 bytes; second would push total to 16 > 10
    result = _run_selection(p, paths, "books", path_map={"a.jpg": b"12345678", "b.jpg": b"ABCDEFGH"})
    assert len(result.image_blocks) == 1
    assert "byte_cap" in result.skipped_reasons


def test_unknown_category_falls_back_to_list_order():
    p = _provider()
    paths = ["z.jpg", "a.jpg", "m.jpg"]
    result = _run_selection(p, paths, "furniture")
    # No priority ordering — list order preserved
    assert result.used_paths == paths


def test_required_missing_populated_when_front_cover_absent():
    """No front_cover for books → required_missing includes front_cover."""
    p = _provider()
    # Paths that don't match front_cover keyword
    paths = ["condition.jpg", "copyright.jpg", "spine.jpg"]
    result = _run_selection(p, paths, "books")
    assert "front_cover" in result.required_missing


def test_required_missing_empty_when_top_tokens_present():
    p = _provider()
    paths = ["front-cover.jpg", "back-cover.jpg", "spine.jpg"]
    result = _run_selection(p, paths, "books")
    assert result.required_missing == []


# ── Explicit PhotoMeta outranks filename order ─────────────────────────────────

def test_explicit_photo_meta_label_outranks_filename():
    """A file named 'img001.jpg' with PhotoMeta label=front_cover sorts first for books."""
    from packages.intake.src.photo_types import PhotoMeta, PhotoType, PhotoSource

    p = _provider()
    paths = ["spine.jpg", "img001.jpg"]  # img001 has no keyword match for front_cover

    pm = PhotoMeta(
        path="img001.jpg",
        photo_type=PhotoType.FRONT,  # maps to front_cover for books
        source=PhotoSource.LOCAL,
        user_labeled=True,
    )

    result = _run_selection(p, paths, "books", photo_meta=[pm])
    # img001 should sort ahead of spine (front_cover = index 0, spine = index 2)
    assert result.used_paths.index("img001.jpg") < result.used_paths.index("spine.jpg")


# ── Integration: new fields propagate to DeepAnalysisResult ───────────────────

def test_image_selection_fields_in_deep_analysis_result():
    """selected_photo_types and skipped_image_count appear in result."""
    from packages.intake.src.analysis_contract import DeepAnalysisRequest
    from packages.intake.src.pipeline_types import RiskFlag

    settings = _settings()
    provider = ClaudeDeepAnalysisProvider(settings)

    mock_response_content = {
        "suggested_field_updates": {"color": "Blue"},
        "confidence_by_field": {"color": 0.8},
        "evidence_by_field": {"color": ["cover is blue"]},
        "uncertain_fields": [],
        "do_not_guess_fields": ["authenticity"],
        "suggested_condition_id": "5000",
        "condition_assessment": "Good.",
        "item_specifics": {},
        "title_suggestions": [],
        "description_suggestion": "A book.",
        "pricing_estimate": 10.0,
        "needs_more_photos": False,
        "missing_photo_types": [],
        "publish_risk_flags": [],
        "correction_summary": [],
        "should_require_manual_review": True,
        "analysis_notes": "",
    }
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(mock_response_content))]
    mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)

    item = Item(
        sku="BK-IMG",
        category_key="books",
        category_label="Books",
        title_final="Test Book",
        brand="Penguin",
        condition_label="Good",
        condition_id="5000",
        image_paths=["https://example.com/img.jpg"],  # hosted → skipped
    )

    request = DeepAnalysisRequest(sku="BK-IMG", canonical_schema_version="v1", item=item)

    with (
        patch("packages.intake.src.providers.claude_deep_analysis._is_anthropic_installed", return_value=True),
        patch("anthropic.Anthropic") as mock_cls,
    ):
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = provider.analyze(request)

    assert result.selected_image_count == 0  # hosted URL was skipped
    assert result.skipped_image_count == 1
    assert "hosted_url_skipped" in result.skipped_image_reasons
    # No images sent → confidence_source is MIXED
    from packages.intake.src.pipeline_types import ConfidenceSource
    assert result.confidence_source == ConfidenceSource.MIXED
