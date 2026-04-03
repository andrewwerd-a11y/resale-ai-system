"""Unit tests for the SKU registry."""
import pytest
from packages.sku.src.registry import SKURegistry


def test_suggest_next_known_prefix(test_session):
    registry = SKURegistry(test_session)
    result = registry.suggest_next("CL")
    assert result.ok
    assert result.value == "CL-000001"


def test_suggest_next_zero_padded_six_digits(test_session):
    registry = SKURegistry(test_session)
    result = registry.suggest_next("BK")
    assert result.ok
    # Format must be XX-NNNNNN
    parts = result.value.split("-")
    assert len(parts) == 2
    assert len(parts[1]) == 6


def test_suggest_next_unknown_prefix_returns_failure(test_session):
    registry = SKURegistry(test_session)
    result = registry.suggest_next("ZZ")
    assert not result.ok
    assert "Unknown prefix" in result.error


def test_reserve_increments_counter(test_session):
    registry = SKURegistry(test_session)
    first = registry.reserve("CL")
    second = registry.reserve("CL")
    assert first.ok and second.ok
    assert first.value == "CL-000001"
    assert second.value == "CL-000002"


def test_reserve_no_duplicates_across_calls(test_session):
    registry = SKURegistry(test_session)
    skus = [registry.reserve("CL").value for _ in range(5)]
    assert len(set(skus)) == 5


def test_reserve_unknown_prefix_returns_failure(test_session):
    registry = SKURegistry(test_session)
    result = registry.reserve("ZZ")
    assert not result.ok


def test_preserve_existing_sku(test_session):
    registry = SKURegistry(test_session)
    # Simulating migration: CL-000007 already exists
    result = registry.preserve_existing_sku("CL-000007")
    assert result.ok
    assert result.value == "CL-000007"
    # Next reserved SKU must be above 7
    next_sku = registry.reserve("CL")
    assert next_sku.ok
    num = int(next_sku.value.split("-")[1])
    assert num > 7


def test_preserve_existing_never_goes_below_highest(test_session):
    registry = SKURegistry(test_session)
    registry.preserve_existing_sku("CL-000050")
    registry.preserve_existing_sku("CL-000020")  # lower — must not regress
    next_sku = registry.reserve("CL")
    assert next_sku.ok
    num = int(next_sku.value.split("-")[1])
    assert num == 51  # one above the highest (50)


def test_parse_sku_valid(test_session):
    registry = SKURegistry(test_session)
    parsed = registry.repo.parse_sku("CL-000007")
    assert parsed == ("CL", 7)


def test_parse_sku_invalid_returns_none(test_session):
    registry = SKURegistry(test_session)
    assert registry.repo.parse_sku("invalid") is None
    assert registry.repo.parse_sku("") is None
    assert registry.repo.parse_sku("CL-abc") is None


def test_is_valid_sku_known_prefix(test_session):
    registry = SKURegistry(test_session)
    assert registry.is_valid_sku("CL-000001") is True
    assert registry.is_valid_sku("BK-000042") is True


def test_is_valid_sku_unknown_prefix(test_session):
    registry = SKURegistry(test_session)
    assert registry.is_valid_sku("ZZ-000001") is False


def test_initialize_from_scan(test_session):
    registry = SKURegistry(test_session)
    highest = registry.initialize_from_scan(["CL-000010", "CL-000005", "BK-000003"])
    assert highest["CL"] == 10
    assert highest["BK"] == 3
    # Next SKU must be above highest
    next_cl = registry.reserve("CL")
    assert int(next_cl.value.split("-")[1]) == 11


def test_different_prefixes_independent(test_session):
    registry = SKURegistry(test_session)
    registry.preserve_existing_sku("CL-000010")
    # BK counter is unaffected
    next_bk = registry.reserve("BK")
    assert next_bk.value == "BK-000001"
