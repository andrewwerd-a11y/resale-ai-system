"""Unit tests for the vision ResponseParser."""
import pytest
from packages.vision.src.response_parser import ResponseParser, _dedup
from packages.core.src.constants import ReviewTrigger


@pytest.fixture
def parser():
    return ResponseParser()


# ── _dedup helper ─────────────────────────────────────────────────────────────

def test_dedup_removes_duplicates():
    assert _dedup(["a", "b", "a", "c"]) == ["a", "b", "c"]


def test_dedup_preserves_order():
    assert _dedup(["c", "a", "b"]) == ["c", "a", "b"]


def test_dedup_handles_empty():
    assert _dedup([]) == []


def test_dedup_tolerates_unhashable_items():
    # Dicts are unhashable — _dedup must not crash
    result = _dedup([{"a": 1}, {"b": 2}, {"a": 1}])
    assert len(result) == 2


def test_dedup_no_dicts_added_to_string_list():
    result = _dedup(["low_confidence", "low_confidence"])
    assert result == ["low_confidence"]


# ── condition_id coercion ─────────────────────────────────────────────────────

def test_condition_id_int_coerced_to_string(parser):
    raw = {"condition_id": 5000, "confidence_score": 0.80, "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.ok
    assert result.value["condition_id"] == "5000"
    assert isinstance(result.value["condition_id"], str)


def test_condition_id_string_preserved(parser):
    raw = {"condition_id": "3000", "confidence_score": 0.80, "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.value["condition_id"] == "3000"


# ── confidence clamping ───────────────────────────────────────────────────────

def test_confidence_clamped_below_zero(parser):
    raw = {"confidence_score": -0.5, "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.value["confidence_score"] == 0.0


def test_confidence_clamped_above_one(parser):
    raw = {"confidence_score": 1.5, "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.value["confidence_score"] == 1.0


def test_confidence_string_parsed(parser):
    # Model sometimes returns "0.85 out of 1.0"
    raw = {"confidence_score": "0.85", "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.ok
    assert result.value["confidence_score"] == pytest.approx(0.85)


def test_confidence_list_takes_first(parser):
    raw = {"confidence_score": [0.75, 0.80], "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.value["confidence_score"] == pytest.approx(0.75)


def test_confidence_none_defaults_to_zero(parser):
    raw = {"confidence_score": None, "estimated_price": 10.0}
    result = parser.parse(raw, "clothing")
    assert result.value["confidence_score"] == 0.0


# ── required fields → needs_review ───────────────────────────────────────────

def test_missing_required_fields_triggers_review(parser):
    # clothing requires brand, type, department, size, color, condition_label, condition_id
    raw = {
        "confidence_score": 0.80,
        "estimated_price": 10.0,
        # brand, type, department, size missing
        "color": "Blue",
        "condition_label": "Good",
        "condition_id": "3000",
    }
    result = parser.parse(raw, "clothing")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.MISSING_REQUIRED_FIELDS in result.value["review_reasons"]


def test_all_required_fields_present_no_review_for_fields(parser):
    raw = {
        "confidence_score": 0.80,
        "estimated_price": 10.0,
        "brand": "Nike",
        "type": "Jacket",
        "department": "Men",
        "size": "L",
        "color": "Blue",
        "condition_label": "Good",
        "condition_id": "3000",
    }
    result = parser.parse(raw, "clothing")
    assert ReviewTrigger.MISSING_REQUIRED_FIELDS not in result.value.get("review_reasons", [])


# ── low confidence → needs_review ────────────────────────────────────────────

def test_low_confidence_triggers_review(parser):
    raw = {
        "confidence_score": 0.50,   # below 0.72 threshold
        "estimated_price": 10.0,
        "brand": "Nike", "type": "Jacket", "department": "Men",
        "size": "L", "color": "Blue", "condition_label": "Good", "condition_id": "3000",
    }
    result = parser.parse(raw, "clothing")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.LOW_CONFIDENCE in result.value["review_reasons"]


def test_confidence_at_threshold_not_flagged(parser):
    raw = {
        "confidence_score": 0.72,   # exactly at threshold — not below
        "estimated_price": 10.0,
        "brand": "Nike", "type": "Jacket", "department": "Men",
        "size": "L", "color": "Blue", "condition_label": "Good", "condition_id": "3000",
    }
    result = parser.parse(raw, "clothing")
    assert ReviewTrigger.LOW_CONFIDENCE not in result.value.get("review_reasons", [])


# ── luxury brand → needs_review ──────────────────────────────────────────────

def test_luxury_brand_triggers_review(parser):
    raw = {
        "confidence_score": 0.88,
        "estimated_price": 120.0,
        "brand": "Gucci",
        "type": "Polo", "department": "Men", "size": "L",
        "color": "White", "condition_label": "Like New", "condition_id": "1500",
    }
    result = parser.parse(raw, "clothing")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.LUXURY_BRAND in result.value["review_reasons"]


def test_non_luxury_brand_not_flagged(parser):
    raw = {
        "confidence_score": 0.88,
        "estimated_price": 25.0,
        "brand": "Nike",
        "type": "Jacket", "department": "Men", "size": "L",
        "color": "Black", "condition_label": "Good", "condition_id": "3000",
    }
    result = parser.parse(raw, "clothing")
    assert ReviewTrigger.LUXURY_BRAND not in result.value.get("review_reasons", [])


# ── signed / antique keyword triggers ────────────────────────────────────────

def test_signed_keyword_triggers_review(parser):
    raw = {
        "confidence_score": 0.85,
        "estimated_price": 15.0,
        "title_raw": "Signed first edition hardcover",
        "condition_notes": "",
    }
    result = parser.parse(raw, "books")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.SIGNED in result.value["review_reasons"]


def test_antique_keyword_triggers_review(parser):
    raw = {
        "confidence_score": 0.85,
        "estimated_price": 20.0,
        "title_raw": "Antique Victorian plate",
        "condition_notes": "",
    }
    result = parser.parse(raw, "collectibles")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.ANTIQUE in result.value["review_reasons"]


def test_first_edition_triggers_review(parser):
    raw = {
        "confidence_score": 0.85,
        "estimated_price": 12.0,
        "title_raw": "First edition of classic novel",
        "condition_notes": "",
    }
    result = parser.parse(raw, "books")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.FIRST_EDITION in result.value["review_reasons"]


# ── list field normalisation ──────────────────────────────────────────────────

def test_non_list_review_reasons_becomes_list(parser):
    raw = {"confidence_score": 0.50, "estimated_price": 5.0, "review_reasons": None}
    result = parser.parse(raw, "clothing")
    assert isinstance(result.value["review_reasons"], list)


def test_non_list_features_becomes_list(parser):
    raw = {"confidence_score": 0.80, "estimated_price": 5.0, "features": "pockets"}
    result = parser.parse(raw, "clothing")
    assert isinstance(result.value["features"], list)


def test_measurements_normalised_to_dict(parser):
    raw = {"confidence_score": 0.80, "estimated_price": 5.0, "measurements": None}
    result = parser.parse(raw, "clothing")
    assert isinstance(result.value["measurements"], dict)


# ── high value → needs_review ────────────────────────────────────────────────

def test_high_value_triggers_review(parser):
    raw = {
        "confidence_score": 0.90,
        "estimated_price": 80.0,   # above 75 threshold
        "brand": "Columbia", "type": "Jacket", "department": "Men",
        "size": "L", "color": "Red", "condition_label": "Good", "condition_id": "3000",
    }
    result = parser.parse(raw, "clothing")
    assert result.value["needs_review"] is True
    assert ReviewTrigger.HIGH_VALUE_ESTIMATE in result.value["review_reasons"]


def test_price_list_coerced(parser):
    raw = {"confidence_score": 0.80, "estimated_price": [25.0, 30.0]}
    result = parser.parse(raw, "clothing")
    assert result.value["estimated_price"] == pytest.approx(25.0)
