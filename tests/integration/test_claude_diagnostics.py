from __future__ import annotations

import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items, settings
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "claude_diagnostics.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item():
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(
            Item(
                sku="BK-000005",
                title_final="Existing title",
                description_final="Existing description.",
                brand="Test Brand",
                status="approved",
            )
        )


def _install_fake_anthropic(monkeypatch, *, create_impl):
    class _FakeMessages:
        def create(self, **kwargs):
            return create_impl(**kwargs)

    class _FakeAnthropicClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = _FakeMessages()

    fake_module = types.SimpleNamespace(Anthropic=_FakeAnthropicClient)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.setattr(
        "apps.api.src.services.claude_diagnostics.is_anthropic_package_installed",
        lambda: True,
    )


def test_claude_readiness_and_suggest_report_missing_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    with _client() as client:
        readiness = client.get("/api/settings/claude-readiness")
        suggest = client.post("/api/items/BK-000005/claude-suggest", json={"type": "description"})

    assert readiness.status_code == 200
    readiness_body = readiness.json()
    assert readiness_body["code"] == "missing_api_key"
    assert readiness_body["checks"]["api_key_present"] is False

    assert suggest.status_code == 503
    detail = suggest.json()["detail"]
    assert detail["code"] == "missing_api_key"
    assert "ANTHROPIC_API_KEY" in detail["next_action"]


def test_claude_suggest_connection_error_is_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()
    _install_fake_anthropic(monkeypatch, create_impl=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Connection error")))

    with _client() as client:
        resp = client.post("/api/items/BK-000005/claude-suggest", json={"type": "description"})

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "connection_error"
    assert detail["category"] == "claude"


def test_claude_suggest_auth_error_is_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()
    _install_fake_anthropic(
        monkeypatch,
        create_impl=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Unauthorized: invalid x-api-key")),
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/claude-suggest", json={"type": "description"})

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "auth_error"
    assert "ANTHROPIC_API_KEY" in detail["next_action"]


def test_claude_suggest_rate_limit_is_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()
    _install_fake_anthropic(
        monkeypatch,
        create_impl=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("429 rate limit exceeded")),
    )

    with _client() as client:
        resp = client.post("/api/items/BK-000005/claude-suggest", json={"type": "description"})

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["code"] == "rate_limited"


def test_claude_suggest_mocked_success_still_returns_suggestion(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    def _success(**_kwargs):
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text="Improved description. Clean copy. Light wear. Hardcover edition.")],
        )

    _install_fake_anthropic(monkeypatch, create_impl=_success)

    with _client() as client:
        resp = client.post("/api/items/BK-000005/claude-suggest", json={"type": "description"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "description"
    assert body["suggestion"].startswith("Improved description.")


def test_vision_providers_include_ollama_as_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)

    with _client() as client:
        resp = client.get("/api/settings/vision-providers")

    assert resp.status_code == 200
    body = resp.json()
    assert body["default_provider_id"] == "ollama"
    ollama = next(provider for provider in body["providers"] if provider["id"] == "ollama")
    assert ollama["default"] is True
    assert ollama["active"] is True
    assert ollama["status"] == "available"


def test_vision_providers_include_claude_as_premium_not_selectable(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "apps.api.src.services.claude_diagnostics.is_anthropic_package_installed",
        lambda: True,
    )

    with _client() as client:
        resp = client.get("/api/settings/vision-providers")

    assert resp.status_code == 200
    body = resp.json()
    claude = next(provider for provider in body["providers"] if provider["id"] == "claude")
    assert claude["tier"] == "premium"
    assert claude["status"] == "planned"
    assert claude["selectable"] is False
    assert claude["implemented"] is False


def test_claude_cannot_become_active_default_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")
    core_config.get_settings.cache_clear()
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "apps.api.src.services.claude_diagnostics.is_anthropic_package_installed",
        lambda: True,
    )

    with _client() as client:
        current = client.get("/api/settings/current")
        providers = client.get("/api/settings/vision-providers")

    assert current.status_code == 200
    assert providers.status_code == 200
    assert current.json()["vision_provider_default"] == "ollama"
    assert providers.json()["active_provider_id"] == "ollama"
