"""
Central settings — loaded once at startup from .env.
All packages import from here; nothing reads os.environ directly.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[3]   # repo root
CONFIG_DIR = ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    intake_root: Path = Field(default=ROOT / "intake")
    db_path: Path = Field(default=ROOT / "data" / "app.db")
    export_dir: Path = Field(default=ROOT / "data" / "exports")
    import_dir: Path = Field(default=ROOT / "data" / "imports")
    log_dir: Path = Field(default=ROOT / "data" / "logs")

    # Vision
    ollama_base_url: str = "http://localhost:11434"
    vision_model_default: str = "minicpm-v"
    vision_model_fallback: str = "minicpm-v"
    vision_model_premium: str = "llama3.2-vision:11b"

    # Thresholds
    confidence_review_threshold: float = 0.72
    high_value_review_threshold: float = 75.00

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = True

    # Logging
    log_level: str = "INFO"

    # Dev
    dry_run: bool = False

    # eBay Sandbox
    ebay_sandbox_app_id: str = ""
    ebay_sandbox_cert_id: str = ""
    ebay_sandbox_dev_id: str = ""
    ebay_sandbox_user_token: str = ""

    # eBay Production
    ebay_prod_app_id: str = ""
    ebay_prod_cert_id: str = ""
    ebay_prod_dev_id: str = ""
    ebay_prod_user_token: str = ""

    # eBay Shared
    ebay_runame: str = ""
    ebay_environment: str = "sandbox"
    ebay_marketplace_id: str = "EBAY_US"

    # Photo hosting
    imgur_client_id: str = ""

    @property
    def ebay_app_id(self) -> str:
        return self.ebay_prod_app_id if self.ebay_environment == "production" else self.ebay_sandbox_app_id

    @property
    def ebay_cert_id(self) -> str:
        return self.ebay_prod_cert_id if self.ebay_environment == "production" else self.ebay_sandbox_cert_id

    @property
    def ebay_dev_id(self) -> str:
        return self.ebay_prod_dev_id if self.ebay_environment == "production" else self.ebay_sandbox_dev_id

    @property
    def ebay_user_token(self) -> str:
        return self.ebay_prod_user_token if self.ebay_environment == "production" else self.ebay_sandbox_user_token

    @property
    def ebay_api_base(self) -> str:
        if self.ebay_environment == "production":
            return "https://api.ebay.com"
        return "https://api.sandbox.ebay.com"

    def ensure_dirs(self) -> None:
        for path in [
            self.intake_root / "pending",
            self.intake_root / "processing",
            self.intake_root / "processed",
            self.intake_root / "review",
            self.intake_root / "rejected",
            self.intake_root / "archived",
            self.export_dir,
            self.import_dir,
            self.log_dir,
            self.db_path.parent,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_sku_prefixes() -> dict:
    path = CONFIG_DIR / "sku_prefixes.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def get_categories() -> dict:
    path = CONFIG_DIR / "categories.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def get_rules() -> dict:
    path = CONFIG_DIR / "rules.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def get_ebay_fields() -> dict:
    path = CONFIG_DIR / "ebay_fields.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def get_model_profiles() -> dict:
    path = CONFIG_DIR / "model_profiles.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)