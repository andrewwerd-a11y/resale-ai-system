from __future__ import annotations
from pathlib import Path


_PROMPT_DIR = Path(__file__).parent / "prompts"


def build_extraction_prompt(category_hint: str | None = None) -> str:
    prompt_path = _PROMPT_DIR / "extraction_v1.txt"
    base = prompt_path.read_text()
    if category_hint:
        base += f"\n\nHint: This item is likely in the '{category_hint}' category."
    return base
