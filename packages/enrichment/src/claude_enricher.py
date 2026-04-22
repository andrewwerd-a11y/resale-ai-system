"""
Claude vision enricher — sends photos directly to Claude Sonnet and returns
structured listing data without requiring a prior vision model pass.

Uses up to 3 photos (best clarity first — caller should pre-sort via photo_scorer).
Handles base64 encoding internally.  Returns a plain dict on success, raises on error.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = "You are an eBay listing expert. Return ONLY valid JSON, no markdown."

_USER_PROMPT = """\
Identify this item and return:
{
  "title": "eBay-optimized title, under 80 characters, keywords first",
  "description": "2-3 sentences suitable for an eBay listing body",
  "category_suggestion": "suggested eBay category name",
  "brand": "brand name or null",
  "model": "model name or null",
  "condition_notes": "brief condition description",
  "estimated_price": 0.00,
  "confidence": 0.0
}"""

# Maximum photos to send — more increases token cost without much gain
_MAX_PHOTOS = 3


def claude_enrich(
    photo_paths: list[Path],
    seed: Optional[dict] = None,
) -> dict:
    """
    Call Claude Sonnet vision and return a structured enrichment dict.

    Args:
        photo_paths: Ordered list of photo paths (best first).  Up to _MAX_PHOTOS used.
        seed:        Optional dict from a prior local_enrich pass.  When provided, it is
                     appended to the user message so Claude can refine rather than start cold.

    Returns:
        dict with keys: title, description, category_suggestion, brand, model,
                        condition_notes, estimated_price, confidence
                        plus "_input_tokens", "_output_tokens", "_cost_usd" metadata.

    Raises:
        RuntimeError: if anthropic is not installed or no readable photos are found.
        ValueError:   if Claude returns unparseable JSON.
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed — run: uv sync")

    from packages.core.src.config import get_settings
    settings = get_settings()

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Build image content blocks
    image_blocks: list[dict] = []
    for path in photo_paths[:_MAX_PHOTOS]:
        if not path.exists():
            logger.debug("Photo missing, skipping: %s", path)
            continue
        try:
            with open(path, "rb") as fh:
                b64 = base64.standard_b64encode(fh.read()).decode()
            ext = path.suffix.lower().lstrip(".")
            media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            })
        except Exception as exc:
            logger.warning("Could not read photo %s: %s", path, exc)

    if not image_blocks:
        raise RuntimeError("No readable photos found for Claude enrichment")

    # Build the full user message
    user_parts: list[dict] = image_blocks + [{"type": "text", "text": _USER_PROMPT}]

    if seed:
        seed_text = json.dumps(seed, indent=2, default=str)
        user_parts.append({
            "type": "text",
            "text": f"\nExisting extracted data (refine or override as needed):\n{seed_text}",
        })

    response = client.messages.create(
        model=settings.enrichment_model,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_parts}],
    )

    raw = response.content[0].text.strip()
    # Strip accidental markdown fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = []
        for line in lines[1:]:
            if line.strip() == "```":
                break
            inner.append(line)
        raw = "\n".join(inner)

    try:
        enriched = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned unparseable JSON: {exc}\nRaw: {raw[:500]}") from exc

    # Attach token / cost metadata so callers can log it
    input_tok = response.usage.input_tokens
    output_tok = response.usage.output_tokens
    input_price = settings.claude_input_price
    output_price = settings.claude_output_price
    cost = round(input_tok * input_price + output_tok * output_price, 6)

    enriched["_input_tokens"] = input_tok
    enriched["_output_tokens"] = output_tok
    enriched["_cost_usd"] = cost

    logger.info(
        "claude_enrich — %d+%d tokens, ~$%.4f",
        input_tok, output_tok, cost,
    )
    return enriched
