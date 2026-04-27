from __future__ import annotations

from apps.api.src.services.claude_diagnostics import get_claude_readiness
from packages.core.src.config import get_settings


def get_vision_provider_options() -> dict:
    settings = get_settings()
    claude_readiness = get_claude_readiness()

    claude_status = "planned"
    claude_note = "Claude vision intake is not wired into the intake worker yet."
    if not claude_readiness["checks"].get("api_key_present"):
        claude_status = "not_configured"
        claude_note = "Claude is a premium future intake option and is not configured yet."

    providers = [
        {
            "id": "ollama",
            "label": "Ollama",
            "capability": "vision_intake",
            "tier": "local",
            "default": True,
            "active": True,
            "selectable": True,
            "status": "available",
            "implemented": True,
            "model": settings.vision_model_default,
            "note": "Default local intake vision provider.",
        },
        {
            "id": "claude",
            "label": "Claude",
            "capability": "vision_intake",
            "tier": "premium",
            "default": False,
            "active": False,
            "selectable": False,
            "status": claude_status,
            "implemented": False,
            "model": claude_readiness["checks"].get("model") or settings.enrichment_model,
            "note": claude_note,
            "selection_block_reason": "Claude intake vision is not implemented yet, so Ollama remains the active default.",
            "readiness": {
                "code": claude_readiness["code"],
                "message": claude_readiness["message"],
            },
        },
    ]

    return {
        "default_provider_id": "ollama",
        "active_provider_id": "ollama",
        "providers": providers,
    }
