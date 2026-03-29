"""
EbayAuth — checks whether eBay API credentials are present and configured.
Does not make any network calls; credential validation happens in EbayInventoryClient.
"""
from __future__ import annotations

from packages.core.src.config import get_settings


class EbayAuth:
    def __init__(self):
        self.settings = get_settings()

    def is_configured(self) -> bool:
        """Return True if the required eBay credentials are set for the active environment."""
        s = self.settings
        if s.ebay_environment == "production":
            return bool(s.ebay_prod_app_id and s.ebay_prod_cert_id and s.ebay_prod_user_token)
        return bool(s.ebay_sandbox_app_id and s.ebay_sandbox_cert_id and s.ebay_sandbox_user_token)

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
        return self.settings.ebay_user_token

    @property
    def api_base(self) -> str:
        return self.settings.ebay_api_base

    @property
    def marketplace_id(self) -> str:
        return self.settings.ebay_marketplace_id
