"""Builds the extraction prompt for a given item/category context."""
from __future__ import annotations

from pathlib import Path

from packages.core.src.config import get_categories

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt_template(version: str = "v1") -> str:
    path = PROMPT_DIR / f"extraction_{version}.txt"
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_extraction_prompt(category_key: str, version: str = "v1") -> str:
    categories = get_categories()
    profile = categories.get(category_key, {})
    required = ", ".join(profile.get("required_fields", []))
    label = profile.get("label", category_key)
    template = load_prompt_template(version)
    return template.format(
        category_key=category_key,
        category_label=label,
        required_fields=required,
    )
