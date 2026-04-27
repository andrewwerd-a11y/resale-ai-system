from __future__ import annotations

import importlib.util

from packages.core.src.config import get_settings


def is_anthropic_package_installed() -> bool:
    try:
        return importlib.util.find_spec("anthropic") is not None
    except Exception:
        return False


def get_claude_readiness() -> dict:
    settings = get_settings()
    api_key_present = bool((settings.anthropic_api_key or "").strip())
    enrichment_enabled = bool(settings.enrichment_enabled)
    model = (settings.enrichment_model or "").strip()
    package_installed = is_anthropic_package_installed()

    blockers: list[str] = []
    warnings: list[str] = []
    next_actions: list[str] = []

    if not api_key_present:
        blockers.append("ANTHROPIC_API_KEY is not configured.")
        next_actions.append("Add ANTHROPIC_API_KEY to the environment before using Claude features.")
    if not package_installed:
        blockers.append("anthropic package is not installed.")
        next_actions.append("Install the anthropic package in this environment before using Claude features.")
    if not enrichment_enabled:
        warnings.append("Claude enrichment is disabled by ENRICHMENT_ENABLED.")
        next_actions.append("Enable ENRICHMENT_ENABLED when you want Claude enrichment to run.")
    if not model:
        blockers.append("Claude model is not configured.")
        next_actions.append("Set ENRICHMENT_MODEL to a supported Claude model.")

    ready = api_key_present and package_installed and bool(model) and enrichment_enabled
    if ready:
        code = "ready"
        message = "Claude text enrichment is configured for optional use."
        next_action = "Claude routes are ready for manual use."
    elif not api_key_present:
        code = "missing_api_key"
        message = "Claude API key is missing."
        next_action = "Add ANTHROPIC_API_KEY and retry the Claude action."
    elif not package_installed:
        code = "package_not_installed"
        message = "anthropic package is not installed."
        next_action = "Install the anthropic package before using Claude features."
    elif not model:
        code = "model_unavailable"
        message = "Claude model is not configured."
        next_action = "Set ENRICHMENT_MODEL to a supported Claude model."
    else:
        code = "disabled"
        message = "Claude enrichment is currently disabled."
        next_action = "Enable Claude enrichment in configuration when you want to use it."

    return {
        "ready": ready,
        "code": code,
        "category": "claude",
        "message": message,
        "next_action": next_action,
        "checks": {
            "api_key_present": api_key_present,
            "enrichment_enabled": enrichment_enabled,
            "model": model,
            "anthropic_package_installed": package_installed,
        },
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": list(dict.fromkeys(next_actions)),
    }


def classify_claude_error(exc: Exception, *, model: str | None = None) -> dict:
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    type_name = exc.__class__.__name__.lower()
    module_name = getattr(exc.__class__, "__module__", "").lower()

    code = "unknown_claude_error"
    safe_message = "Claude request failed."
    next_action = "Check Claude connectivity, credentials, and model configuration before retrying."

    if "timeout" in lowered or "timeout" in type_name:
        code = "timeout"
        safe_message = "Claude request timed out."
        next_action = "Retry later and verify network stability if timeouts continue."
    elif "connection" in lowered or "connection" in type_name or "apiconnection" in type_name:
        code = "connection_error"
        safe_message = "Claude connection failed."
        next_action = "Verify network access, API key presence, and the configured Claude model."
    elif (
        "authentication" in type_name
        or "permission" in type_name
        or "auth" in lowered
        or "unauthorized" in lowered
        or "forbidden" in lowered
        or "invalid x-api-key" in lowered
        or "api key" in lowered
    ):
        code = "auth_error"
        safe_message = "Claude authentication failed."
        next_action = "Verify ANTHROPIC_API_KEY and account access for the configured model."
    elif "rate" in type_name or "rate limit" in lowered or "too many requests" in lowered or "429" in lowered:
        code = "rate_limited"
        safe_message = "Claude rate limit reached."
        next_action = "Wait briefly, then retry with lower request volume if needed."
    elif (
        "notfound" in type_name
        or "model" in lowered and ("not found" in lowered or "unavailable" in lowered or "does not exist" in lowered)
        or "unknown model" in lowered
    ):
        code = "model_unavailable"
        safe_message = "Configured Claude model is unavailable."
        next_action = "Verify ENRICHMENT_MODEL and your Anthropic account model access."
    elif "anthropic" in module_name:
        code = "unknown_claude_error"
        safe_message = "Claude API request failed."

    return {
        "code": code,
        "category": "claude",
        "message": safe_message,
        "next_action": next_action,
        "model": model or "",
    }
