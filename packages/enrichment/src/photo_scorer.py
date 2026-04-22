"""
Photo scoring pipeline — ranks photos by visual quality before Cloudinary upload.
Best photo ends up at index 0 and becomes the eBay cover image.

Scoring weights:
  Resolution (px²)     30%  — Pillow img.size
  Brightness adequacy  25%  — mean luminance, sweet spot ~140, penalty outside 60–210
  Contrast/detail      20%  — channel stddev as proxy
  Subject clarity      25%  — Ollama minicpm-v returns {"clarity": 1-10}

Clarity call is async-optional: if Ollama is unreachable the weight redistributes
to resolution so the rest of the pipeline is unaffected.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


# ── Individual signal scorers ─────────────────────────────────────────────────

def _score_resolution(img) -> float:
    """Normalize pixel count to 0-1. ~12 MP as reference ceiling."""
    w, h = img.size
    ref = 4032 * 3024  # 12 MP
    return min((w * h) / ref, 1.0)


def _score_brightness(stat) -> float:
    """
    Gaussian-shaped score centred on 140 lum.
    Linear penalty ramps to 0 below 60 and above 210.
    """
    mean_lum = sum(stat.mean[:3]) / 3
    if mean_lum < 60:
        return mean_lum / 60.0
    if mean_lum > 210:
        return max(0.0, 1.0 - (mean_lum - 210) / 45.0)
    return math.exp(-0.5 * ((mean_lum - 140) / 40.0) ** 2)


def _score_contrast(stat) -> float:
    """Normalize per-channel stddev average to 0-1. 80 stddev = fully contrasty."""
    stddev = sum(stat.stddev[:3]) / 3
    return min(stddev / 80.0, 1.0)


def _score_clarity_ollama(path: Path, ollama_url: str = "http://localhost:11434") -> float | None:
    """
    Call Ollama minicpm-v for subject clarity (1-10).
    Returns 0-1 normalised, or None if Ollama is unreachable (graceful fallback).
    """
    import base64

    try:
        with open(path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode()

        payload = {
            "model": "minicpm-v",
            "prompt": (
                "Rate the clarity and sharpness of the main subject in this photo. "
                'Return ONLY valid JSON: {"clarity": <integer 1 to 10>}'
            ),
            "images": [b64],
            "stream": False,
        }
        resp = httpx.post(f"{ollama_url}/api/generate", json=payload, timeout=20.0)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        # Strip any accidental markdown fences
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        parsed = json.loads(raw)
        clarity = float(parsed["clarity"])
        return (clarity - 1.0) / 9.0  # normalise 1-10 → 0-1
    except Exception as exc:
        logger.debug("Clarity scoring via Ollama unavailable for %s: %s", path.name, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def rank_photos(paths: list[Path]) -> list[Path]:
    """
    Score each photo and return the list reordered best-first.
    Index 0 becomes the eBay cover image.

    Args:
        paths: list of local Path objects (missing files get score 0)

    Returns:
        Same paths, reordered by descending composite score.
    """
    from PIL import Image, ImageStat

    from packages.core.src.config import get_settings
    settings = get_settings()
    ollama_url = settings.ollama_base_url

    W_RES = 0.30
    W_BRI = 0.25
    W_CON = 0.20
    W_CLR = 0.25

    scored: list[tuple[float, Path]] = []

    for path in paths:
        if not path.exists():
            logger.warning("Photo not found, scoring 0: %s", path)
            scored.append((0.0, path))
            continue

        try:
            img = Image.open(path).convert("RGB")
            stat = ImageStat.Stat(img)

            res = _score_resolution(img)
            bri = _score_brightness(stat)
            con = _score_contrast(stat)
            clr = _score_clarity_ollama(path, ollama_url)

            if clr is None:
                # Redistribute clarity weight to resolution
                total = res * (W_RES + W_CLR) + bri * W_BRI + con * W_CON
            else:
                total = res * W_RES + bri * W_BRI + con * W_CON + clr * W_CLR

            logger.debug(
                "%s → res=%.2f bri=%.2f con=%.2f clr=%s total=%.3f",
                path.name, res, bri, con,
                f"{clr:.2f}" if clr is not None else "N/A",
                total,
            )
            scored.append((total, path))

        except Exception as exc:
            logger.warning("Failed to score %s: %s", path, exc)
            scored.append((0.0, path))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored]
