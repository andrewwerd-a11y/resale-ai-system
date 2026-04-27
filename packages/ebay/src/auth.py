"""
EbayAuth — manages eBay API credentials and OAuth 2.0 token lifecycle.

Token priority (get_user_token):
  1. OAuth access token from data/ebay_tokens.json (auto-refreshed if expired)
  2. IAF token from .env (EBAY_PROD_USER_TOKEN / EBAY_SANDBOX_USER_TOKEN)
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from packages.core.src.config import get_settings
from packages.ebay.src import http_client as ebay_http

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[4]
TOKENS_FILE = ROOT / "data" / "ebay_tokens.json"

SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
])


class EbayAuth:
    def __init__(self):
        self.settings = get_settings()
        self._last_token_issue_code: str | None = None
        self._last_token_issue_message: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """True if we have app credentials AND a usable token (OAuth or env)."""
        s = self.settings
        return bool(s.ebay_app_id and s.ebay_cert_id and self.get_user_token())

    def get_user_token(self) -> str:
        """
        Return a valid Bearer token. Reads OAuth tokens from file first,
        refreshes automatically if expired, falls back to .env token.
        """
        return self.resolve_user_token()["token"]

    def resolve_user_token(self, *, allow_refresh: bool = True) -> dict[str, str | None]:
        """
        Resolve the preferred bearer token along with safe diagnostics.
        Returns {"token", "source", "issue_code", "issue_message"}.
        """
        self._last_token_issue_code = None
        self._last_token_issue_message = None

        # In dry-run mode we never attempt network token refresh; always use env token.
        if self.settings.dry_run:
            env_token = self.settings.ebay_user_token
            if env_token:
                return {
                    "token": env_token,
                    "source": "env",
                    "issue_code": None,
                    "issue_message": None,
                }
            return self._resolution("", "none", "missing_token", "No eBay user token is configured for dry-run mode.")

        tokens = _load_tokens()
        if tokens:
            expires_at = tokens.get("expires_at")
            if expires_at:
                exp = _parse_dt(expires_at)
                if datetime.now(timezone.utc) < exp - timedelta(minutes=5):
                    return {
                        "token": tokens["access_token"],
                        "source": "oauth",
                        "issue_code": None,
                        "issue_message": None,
                    }
                # Attempt silent refresh
                refresh_token = tokens.get("refresh_token", "")
                if allow_refresh and refresh_token:
                    refreshed = self._refresh_access_token(refresh_token)
                    if refreshed:
                        return {
                            "token": refreshed,
                            "source": "oauth_refresh",
                            "issue_code": None,
                            "issue_message": None,
                        }
                    if self.settings.ebay_user_token:
                        return self._resolution(
                            self.settings.ebay_user_token,
                            "env_fallback",
                            self._last_token_issue_code or "refresh_failed",
                            self._last_token_issue_message or "OAuth token refresh failed; using configured environment token instead.",
                        )
                    return self._resolution(
                        "",
                        "none",
                        self._last_token_issue_code or "refresh_failed",
                        self._last_token_issue_message or "OAuth token refresh failed and no fallback environment token is configured.",
                    )
                if self.settings.ebay_user_token:
                    return self._resolution(
                        self.settings.ebay_user_token,
                        "env_fallback",
                        "expired_or_invalid_access_token",
                        "OAuth access token is expired and no refresh token is available; using configured environment token instead.",
                    )
                return self._resolution(
                    "",
                    "none",
                    "expired_or_invalid_access_token",
                    "OAuth access token is expired and no refresh token is available.",
                )
            elif tokens.get("access_token"):
                return {
                    "token": tokens["access_token"],
                    "source": "oauth",
                    "issue_code": None,
                    "issue_message": None,
                }
        # Fall back to IAF token from .env
        env_token = self.settings.ebay_user_token
        if env_token:
            return {
                "token": env_token,
                "source": "env",
                "issue_code": None,
                "issue_message": None,
            }
        return self._resolution("", "none", "missing_token", "No eBay user token is configured.")

    def get_auth_url(self) -> str:
        """Build the eBay OAuth consent URL to redirect the user to."""
        s = self.settings
        if s.ebay_environment == "production":
            base = "https://auth.ebay.com/oauth2/authorize"
        else:
            base = "https://auth.sandbox.ebay.com/oauth2/authorize"
        redirect_uri = s.ebay_oauth_callback if s.ebay_oauth_callback else s.ebay_runame
        params = urlencode({
            "client_id": s.ebay_app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
        })
        return f"{base}?{params}"

    def exchange_code_for_tokens(self, code: str) -> dict:
        """Exchange an authorization code for access + refresh tokens. Saves to file."""
        s = self.settings
        credentials = _b64(s.ebay_app_id, s.ebay_cert_id)
        resp = ebay_http.post(
            f"{s.ebay_api_base}/identity/v1/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.ebay_runame,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Token exchange failed: {resp.status_code} — {resp.text}")
        data = resp.json()
        now = datetime.now(timezone.utc)
        tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "token_type": data.get("token_type", "Bearer"),
            "expires_at": (now + timedelta(seconds=int(data.get("expires_in", 7200)))).isoformat(),
            "refresh_expires_at": (now + timedelta(seconds=int(data.get("refresh_token_expires_in", 47304000)))).isoformat(),
            "obtained_at": now.isoformat(),
        }
        _save_tokens(tokens)
        logger.info("eBay OAuth tokens obtained and saved to %s", TOKENS_FILE)
        return tokens

    def get_token_status(self) -> dict:
        """Return current OAuth token status for display."""
        tokens = _load_tokens()
        now = datetime.now(timezone.utc)
        if not tokens:
            return {
                "has_oauth_tokens": False,
                "using_env_token": bool(self.settings.ebay_user_token),
                "access_token_valid": False,
                "refresh_token_valid": False,
                "expires_at": None,
                "refresh_expires_at": None,
                "obtained_at": None,
            }
        expires_at = tokens.get("expires_at")
        refresh_expires_at = tokens.get("refresh_expires_at")
        access_valid = False
        if expires_at:
            access_valid = now < _parse_dt(expires_at) - timedelta(minutes=5)
        refresh_valid = False
        if refresh_expires_at:
            refresh_valid = now < _parse_dt(refresh_expires_at)
        return {
            "has_oauth_tokens": True,
            "using_env_token": False,
            "access_token_valid": access_valid,
            "refresh_token_valid": refresh_valid,
            "expires_at": expires_at,
            "refresh_expires_at": refresh_expires_at,
            "obtained_at": tokens.get("obtained_at"),
        }

    # ── Internal ────────────────────────────────────────────────────────────────

    def _refresh_access_token(self, refresh_token: str) -> Optional[str]:
        if not refresh_token:
            return None
        s = self.settings
        try:
            credentials = _b64(s.ebay_app_id, s.ebay_cert_id)
            resp = ebay_http.post(
                f"{s.ebay_api_base}/identity/v1/oauth2/token",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": SCOPES,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                tokens = _load_tokens() or {}
                now = datetime.now(timezone.utc)
                tokens["access_token"] = data["access_token"]
                tokens["token_type"] = data.get("token_type", "Bearer")
                tokens["expires_at"] = (now + timedelta(seconds=int(data.get("expires_in", 7200)))).isoformat()
                if "refresh_token" in data:
                    tokens["refresh_token"] = data["refresh_token"]
                    tokens["refresh_expires_at"] = (now + timedelta(seconds=int(data.get("refresh_token_expires_in", 47304000)))).isoformat()
                _save_tokens(tokens)
                logger.info("eBay access token refreshed successfully")
                return data["access_token"]
            logger.warning("eBay token refresh failed: %s", resp.status_code)
            self._last_token_issue_code = "refresh_failed"
            self._last_token_issue_message = f"OAuth refresh request failed with HTTP {resp.status_code}."
        except Exception as exc:
            logger.error("eBay token refresh error: %s", exc)
            self._last_token_issue_code = "refresh_failed"
            self._last_token_issue_message = f"OAuth refresh request failed: {type(exc).__name__}."
        return None

    # ── Legacy property shims ────────────────────────────────────────────────

    @property
    def app_id(self) -> str:
        return self.settings.ebay_app_id

    @property
    def cert_id(self) -> str:
        return self.settings.ebay_cert_id

    @property
    def dev_id(self) -> str:
        return self.settings.ebay_dev_id

    @property
    def user_token(self) -> str:
        return self.get_user_token()

    @property
    def api_base(self) -> str:
        return self.settings.ebay_api_base

    @property
    def marketplace_id(self) -> str:
        return self.settings.ebay_marketplace_id

    def _resolution(self, token: str, source: str, issue_code: str | None, issue_message: str | None) -> dict[str, str | None]:
        self._last_token_issue_code = issue_code
        self._last_token_issue_message = issue_message
        return {
            "token": token,
            "source": source,
            "issue_code": issue_code,
            "issue_message": issue_message,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_tokens() -> Optional[dict]:
    if TOKENS_FILE.exists():
        try:
            return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _b64(app_id: str, cert_id: str) -> str:
    return base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
