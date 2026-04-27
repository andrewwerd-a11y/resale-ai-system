from __future__ import annotations

import re

ASPECT_VALUE_MAX_LENGTH = 65

_COLOR_WORDS = [
    "black",
    "blue",
    "brown",
    "gold",
    "gray",
    "green",
    "grey",
    "ivory",
    "navy",
    "orange",
    "pink",
    "purple",
    "red",
    "silver",
    "tan",
    "teal",
    "white",
    "yellow",
]

_COLOR_LABELS = {
    "black": "Black",
    "blue": "Blue",
    "brown": "Brown",
    "gold": "Gold",
    "gray": "Gray",
    "green": "Green",
    "grey": "Gray",
    "ivory": "Ivory",
    "navy": "Navy",
    "orange": "Orange",
    "pink": "Pink",
    "purple": "Purple",
    "red": "Red",
    "silver": "Silver",
    "tan": "Tan",
    "teal": "Teal",
    "white": "White",
    "yellow": "Yellow",
}


def normalize_aspect_value(name: str, value: str) -> tuple[str, str | None]:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        return "", None
    if name.lower() != "color":
        return cleaned, None
    if len(cleaned) <= ASPECT_VALUE_MAX_LENGTH:
        return cleaned, None

    extracted_colors: list[str] = []
    lowered = cleaned.lower()
    for color in _COLOR_WORDS:
        if re.search(rf"\b{re.escape(color)}\b", lowered) and color not in extracted_colors:
            extracted_colors.append(color)

    if len(extracted_colors) == 1:
        normalized = _COLOR_LABELS[extracted_colors[0]]
        return normalized, f"Normalized Color from verbose text to '{normalized}'."
    if 2 <= len(extracted_colors) <= 3:
        normalized = "/".join(_COLOR_LABELS[color] for color in extracted_colors)
        return normalized, f"Normalized Color from verbose text to '{normalized}'."
    if len(extracted_colors) > 3:
        normalized = "Multicolor"
        return normalized, f"Normalized Color from verbose text to '{normalized}'."
    return cleaned, None


def normalize_aspects(aspects: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    normalized: dict[str, list[str]] = {}
    warnings: list[str] = []
    for name, values in (aspects or {}).items():
        normalized_values: list[str] = []
        for value in values or []:
            normalized_value, warning = normalize_aspect_value(name, str(value))
            if normalized_value:
                normalized_values.append(normalized_value)
            if warning and warning not in warnings:
                warnings.append(warning)
        if normalized_values:
            normalized[name] = normalized_values
    return normalized, warnings


def validate_aspects(aspects: dict[str, list[str]]) -> dict:
    normalized_aspects, warnings = normalize_aspects(aspects)
    blockers: list[str] = []
    issues: list[dict] = []

    for name, values in normalized_aspects.items():
        for value in values:
            if len(value) <= ASPECT_VALUE_MAX_LENGTH:
                continue
            detail = (
                f"Aspect '{name}' value exceeds eBay's {ASPECT_VALUE_MAX_LENGTH}-character limit: '{value}'."
            )
            blockers.append(detail)
            issues.append(
                {
                    "aspect": name,
                    "value": value,
                    "max_length": ASPECT_VALUE_MAX_LENGTH,
                    "detail": detail,
                }
            )

    return {
        "normalized_aspects": normalized_aspects,
        "warnings": warnings,
        "blockers": blockers,
        "issues": issues,
        "ok": len(blockers) == 0,
    }
