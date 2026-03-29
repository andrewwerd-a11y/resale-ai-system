from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


# parents[3] = repo root (core/src/__init__ → core/src → core → packages → repo)
ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    intake_root: Path = Field(default=ROOT / "intake")
    db_path: Path = Field(default=ROOT / "data" / "app.db")
    export_dir: Path = Field(default=ROOT / "data" / "exports")
    import_dir: Path = Field(default=ROOT / "data" / "imports")

    # Ollama / Vision
    ollama_base_url: str = "http://localhost:11434"
    vision_model_default: str = "minicpm-v"

    # Thresholds
    confidence_review_threshold: float = 0.72
    high_value_review_threshold: float = 75.00

    # API server
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Misc
    log_level: str = "INFO"
    dry_run: bool = False

    # eBay — sandbox keys
    ebay_sandbox_app_id: str = ""
    ebay_sandbox_cert_id: str = ""
    ebay_sandbox_dev_id: str = ""
    ebay_sandbox_user_token: str = ""

    # eBay — production keys
    ebay_prod_app_id: str = ""
    ebay_prod_cert_id: str = ""
    ebay_prod_dev_id: str = ""
    ebay_prod_user_token: str = ""

    # eBay — shared
    ebay_runame: str = ""
    ebay_environment: str = "sandbox"
    ebay_marketplace_id: str = "EBAY_US"

    # Imgur
    imgur_client_id: str = ""

    # ---- computed helpers ----

    @property
    def is_sandbox(self) -> bool:
        return self.ebay_environment != "production"

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

    @property
    def config_dir(self) -> Path:
        return ROOT / "config"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
