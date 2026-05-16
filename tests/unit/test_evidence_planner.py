from __future__ import annotations

from packages.intake.src.evidence_planner import suggest_evidence_hints_from_aspects


def test_brand_aspect_yields_brand_tag_hint():
    result = suggest_evidence_hints_from_aspects(
        category_id="123",
        aspects=[{"name": "Brand"}],
    )
    assert result["read_only"] is True
    assert result["no_live_ebay_call_performed"] is True
    assert result["evidence_hints"][0]["suggested_photo_types"] == ["brand_tag"]


def test_size_aspect_yields_size_or_measurement_hint():
    result = suggest_evidence_hints_from_aspects(
        category_id="123",
        aspects=[{"name": "Size"}],
    )
    assert result["evidence_hints"][0]["suggested_photo_types"] == ["size_tag", "measurement"]


def test_serial_or_mpn_aspect_yields_serial_hint():
    result = suggest_evidence_hints_from_aspects(
        category_id="123",
        aspects=[{"name": "MPN"}, {"name": "Serial Number"}],
    )
    hinted = [hint["suggested_photo_types"] for hint in result["evidence_hints"]]
    assert ["serial_or_date_code", "detail"] in hinted
