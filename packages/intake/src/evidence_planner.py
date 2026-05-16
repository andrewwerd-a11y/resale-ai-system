from __future__ import annotations


def suggest_evidence_hints_from_aspects(
    *,
    category_id: str | None,
    aspects: list[dict] | None,
) -> dict:
    """Deterministic placeholder for aspect-aware evidence planning.

    This is intentionally pure and read-only. It accepts already-available
    category/aspect metadata and suggests photo-evidence hints that could help
    satisfy or verify those aspects later.
    """
    hints: list[dict] = []
    for aspect in aspects or []:
        name = str(aspect.get("name") or "").strip()
        lowered = name.lower()
        if not lowered:
            continue
        if "brand" in lowered:
            hints.append(_hint(name, ["brand_tag"], "recommended", "Brand aspect suggests a brand-tag or logo photo."))
        elif "size" in lowered:
            hints.append(_hint(name, ["size_tag", "measurement"], "recommended", "Size aspect suggests a size tag or measurement photo."))
        elif "material" in lowered or "fabric" in lowered:
            hints.append(_hint(name, ["material_care_tag"], "recommended", "Material aspect suggests a material/care tag photo."))
        elif "mpn" in lowered or "model" in lowered or "serial" in lowered:
            hints.append(_hint(name, ["serial_or_date_code", "detail"], "recommended", "Model/serial aspect suggests a serial/code or detail photo."))
    return {
        "category_id": str(category_id or ""),
        "evidence_hints": hints,
        "read_only": True,
        "no_live_ebay_call_performed": True,
    }


def _hint(aspect_name: str, photo_types: list[str], priority: str, rationale: str) -> dict:
    return {
        "aspect_name": aspect_name,
        "suggested_photo_types": list(photo_types),
        "priority": priority,
        "rationale": rationale,
    }
