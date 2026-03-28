"""
eBay OAuth token manager.

Supports both sandbox and production environments.
Uses IAF (Individual Access/Refresh) user tokens stored in .env for the
primary flow. Also supports client credentials (app token) for public APIs.

Token is selected automatically based on EBAY_ENVIRONMENT=sandbox|production.
"""
from __future__ import annotations
import base64
import time
from typing import Optional

import httpx

from packages.core.src.config import get_settings
from packages.core.src.result import Result


class _AppTokenCache:
    token: Optional[str] = None
    expires_at: float = 0.0


_app_token_cache = _AppTokenCache()


def is_configured() -> bool:
    """Return True if minimum eBay credentials are present."""
    settings = get_settings()
    return bool(settings.ebay_app_id and settings.ebay_cert_id and settings.ebay_user_token)


def get_user_token() -> str:
    """Return the pre-configured IAF user token from .env."""
    settings = get_settings()
    return settings.ebay_user_token


def get_app_token() -> Result[str]:
    """
    Obtain an OAuth application token using Client Credentials grant.
    Result is cached and auto-refreshed when within 60 s of expiry.
    """
    settings = get_settings()

    if _app_token_cache.token and time.time() < _app_token_cache.expires_at - 60:
        return Result.success(_app_token_cache.token)

    app_id = settings.ebay_app_id
    cert_id = settings.ebay_cert_id
    if not app_id or not cert_id:
        return Result.failure("eBay App ID or Cert ID not configured")

    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()

    token_url = f"{settings.ebay_api_base}/identity/v1/oauth2/token"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                token_url,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return Result.failure(f"eBay token request failed: {e}")

    token = data.get("access_token")
    expires_in = int(data.get("expires_in", 7200))

    if not token:
        return Result.failure(f"eBay token response missing access_token: {data}")

    _app_token_cache.token = token
    _app_token_cache.expires_at = time.time() + expires_in
    return Result.success(token)


def user_token_headers() -> dict[str, str]:
    """Return Authorization + Content-Type headers for user-scoped API calls."""
    settings = get_settings()
    return {
        "Authorization": f"Bearer {get_user_token()}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace_id,
        "Accept": "application/json",
    }


def environment_name() -> str:
    settings = get_settings()
    return "sandbox" if settings.is_sandbox else "production"
