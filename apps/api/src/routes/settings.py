"""
Settings API — read/write config files and return current settings.
Changes to rules.json and platforms.json take effect on next request.
Changes to .env fields require server restart.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_ROOT = Path(__file__).resolve().parents[4]
_CONFIG = _ROOT / "config"
_RULES_FILE = _CONFIG / "rules.json"
_PLATFORMS_FILE = _CONFIG / "platforms.json"


class RulesUpdate(BaseModel):
    triage: dict | None = None
    pricing: dict | None = None


class PlatformToggle(BaseModel):
    active: bool


@router.get("/rules")
def get_rules_config():
    with open(_RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


@router.patch("/rules")
def update_rules_config(body: RulesUpdate):
    with open(_RULES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if body.triage:
        data.setdefault("triage", {}).update(body.triage)
    if body.pricing:
        data.setdefault("pricing", {}).update(body.pricing)
    with open(_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    from packages.core.src.config import get_rules
    get_rules.cache_clear()
    return data


@router.get("/platforms")
def get_platforms_config():
    with open(_PLATFORMS_FILE, encoding="utf-8") as f:
        return json.load(f)


@router.patch("/platforms/{platform_key}")
def update_platform_config(platform_key: str, body: PlatformToggle):
    with open(_PLATFORMS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if platform_key not in data:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_key}' not found")
    data[platform_key]["active"] = body.active
    with open(_PLATFORMS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data[platform_key]


@router.get("/current")
def get_current_settings():
    """Return non-sensitive runtime config values."""
    from packages.core.src.config import get_settings
    settings = get_settings()
    return {
        "ebay_environment": settings.ebay_environment,
        "enrichment_enabled": settings.enrichment_enabled,
        "enrichment_model": settings.enrichment_model,
        "vision_model_default": settings.vision_model_default,
        "vision_model_fallback": settings.vision_model_fallback,
        "vision_model_premium": settings.vision_model_premium,
        "confidence_review_threshold": settings.confidence_review_threshold,
        "high_value_review_threshold": settings.high_value_review_threshold,
        "notifications_enabled": getattr(settings, "notifications_enabled", False),
        "notify_email": getattr(settings, "notify_email", ""),
        "smtp_host": getattr(settings, "smtp_host", ""),
        "smtp_port": getattr(settings, "smtp_port", 587),
    }
