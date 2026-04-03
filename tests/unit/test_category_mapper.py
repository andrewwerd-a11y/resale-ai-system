"""Unit tests for CategoryMapper."""
import pytest
from packages.classification.src.category_mapper import CategoryMapper


@pytest.fixture
def mapper():
    return CategoryMapper()


# ── from_prefix ───────────────────────────────────────────────────────────────

def test_bk_prefix_returns_books(mapper):
    result = mapper.from_prefix("BK")
    assert result.ok
    assert result.value["category_key"] == "books"


def test_cl_prefix_returns_clothing(mapper):
    result = mapper.from_prefix("CL")
    assert result.ok
    assert result.value["category_key"] == "clothing"


def test_co_prefix_returns_collectibles(mapper):
    result = mapper.from_prefix("CO")
    assert result.ok
    assert result.value["category_key"] == "collectibles"


def test_sh_prefix_returns_shoes(mapper):
    result = mapper.from_prefix("SH")
    assert result.ok
    assert result.value["category_key"] == "shoes"


def test_to_prefix_returns_toys(mapper):
    result = mapper.from_prefix("TO")
    assert result.ok
    assert result.value["category_key"] == "toys"


def test_unknown_prefix_returns_failure(mapper):
    result = mapper.from_prefix("ZZ")
    assert not result.ok
    assert "Unknown prefix" in result.error


# ── from_sku ──────────────────────────────────────────────────────────────────

def test_from_sku_clothing(mapper):
    result = mapper.from_sku("CL-000007")
    assert result.ok
    assert result.value["category_key"] == "clothing"


def test_from_sku_books(mapper):
    result = mapper.from_sku("BK-000042")
    assert result.ok
    assert result.value["category_key"] == "books"


def test_from_sku_malformed_returns_failure(mapper):
    result = mapper.from_sku("invalid")
    assert not result.ok


def test_from_sku_unknown_prefix_returns_failure(mapper):
    result = mapper.from_sku("ZZ-000001")
    assert not result.ok


# ── required / optional fields ────────────────────────────────────────────────

def test_clothing_required_fields_not_empty(mapper):
    fields = mapper.required_fields("clothing")
    assert len(fields) > 0
    assert "brand" in fields
    assert "size" in fields


def test_books_required_fields_not_empty(mapper):
    fields = mapper.required_fields("books")
    assert "author" in fields
    assert "format" in fields


def test_unknown_category_required_fields_empty(mapper):
    assert mapper.required_fields("unknown_cat") == []


# ── title template ────────────────────────────────────────────────────────────

def test_build_title_clothing(mapper):
    fields = {
        "brand": "Nike", "type": "Jacket", "department": "Men",
        "size": "L", "color": "Blue",
    }
    title = mapper.build_title("clothing", fields)
    assert "Nike" in title
    assert "Jacket" in title
    assert len(title) <= 80


def test_build_title_with_missing_fields_no_crash(mapper):
    # Missing required template fields cause a KeyError fallback — must not crash.
    # The fallback returns title_raw / title_final / "" when keys are absent.
    fields = {"brand": "Nike"}
    title = mapper.build_title("clothing", fields)
    assert isinstance(title, str)  # must never raise


def test_build_title_truncated_to_80(mapper):
    fields = {
        "brand": "A" * 40,
        "type": "B" * 40,
        "department": "Men",
        "size": "L",
        "color": "Blue",
    }
    title = mapper.build_title("clothing", fields)
    assert len(title) <= 80


# ── result contains prefix ────────────────────────────────────────────────────

def test_from_prefix_result_contains_label(mapper):
    result = mapper.from_prefix("CL")
    assert result.value.get("category_label") is not None


def test_from_prefix_result_contains_prefix(mapper):
    result = mapper.from_prefix("BK")
    assert result.value["prefix"] == "BK"
