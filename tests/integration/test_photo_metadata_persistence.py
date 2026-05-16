from __future__ import annotations

from sqlmodel import Session, create_engine

from apps.api.src.services.photo_metadata import load_photo_metadata, upsert_photo_labels
from packages.intake.src.providers.claude_deep_analysis import ClaudeDeepAnalysisProvider
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_photo_metadata_repo import ItemPhotoMetadataRepository
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from packages.intake.src.pipeline_types import PhotoLabelSource, PhotoType


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "photo_metadata_persistence.db"
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


def _seed_item(**overrides) -> None:
    base = dict(
        sku="BK-META",
        status=ItemStatus.NEEDS_REVIEW,
        title_final="A Book",
        category_key="books",
        image_paths=["front-cover.jpg", "mystery.jpg"],
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def test_photo_metadata_can_be_created_and_updated(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-META")
        assert item is not None
        first = upsert_photo_labels(
            session,
            item,
            [{"image_path": "mystery.jpg", "photo_type": PhotoType.SPINE, "notes": "operator label"}],
        )
        second = upsert_photo_labels(
            session,
            item,
            [{"image_path": "mystery.jpg", "photo_type": PhotoType.TITLE_PAGE, "notes": "updated label"}],
        )
        rows = ItemPhotoMetadataRepository(session).list_for_sku("BK-META")

    assert len(rows) == 1
    assert any(meta.path == "mystery.jpg" and meta.photo_type == PhotoType.SPINE for meta in first)
    updated = next(meta for meta in second if meta.path == "mystery.jpg")
    assert updated.photo_type == PhotoType.TITLE_PAGE
    assert updated.label_source == PhotoLabelSource.USER_LABELED
    assert updated.notes == "updated label"


def test_missing_metadata_falls_back_to_filename_inference(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["front-cover.jpg", "spine.jpg"])

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-META")
        assert item is not None
        metas = load_photo_metadata(session, item)

    by_path = {meta.path: meta for meta in metas}
    assert by_path["front-cover.jpg"].photo_type == PhotoType.FRONT
    assert by_path["front-cover.jpg"].label_source == PhotoLabelSource.FILENAME_INFERRED
    assert by_path["spine.jpg"].photo_type == PhotoType.SPINE


def test_user_labeled_metadata_outranks_filename_inference(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["front-cover.jpg"])

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-META")
        assert item is not None
        metas = upsert_photo_labels(
            session,
            item,
            [{"image_path": "front-cover.jpg", "photo_type": PhotoType.BACK}],
        )

    assert metas[0].path == "front-cover.jpg"
    assert metas[0].photo_type == PhotoType.BACK
    assert metas[0].user_labeled is True


def test_existing_image_paths_behavior_remains_intact(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["front-cover.jpg", "back-cover.jpg"])

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-META")
        assert item is not None
        metas = load_photo_metadata(session, item)
        reloaded = ItemRepository(session).get_by_sku("BK-META")

    assert [meta.path for meta in metas] == ["front-cover.jpg", "back-cover.jpg"]
    assert reloaded is not None
    assert reloaded.image_paths == ["front-cover.jpg", "back-cover.jpg"]


def test_claude_selection_prefers_stored_labels(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["spine.jpg", "img001.jpg"])

    class _Settings:
        intake_external_provider_enabled = True
        intake_provider = "claude"
        intake_model = ""
        enrichment_model = "claude-sonnet-4-20250514"
        anthropic_api_key = "sk-test"
        intake_max_images_default = 5
        intake_max_images_books = 6
        intake_max_images_clothing = 6
        intake_max_images_bags = 7
        intake_max_images_toys = 5
        intake_max_image_bytes_total = 10 * 1024 * 1024

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-META")
        assert item is not None
        upsert_photo_labels(
            session,
            item,
            [{"image_path": "img001.jpg", "photo_type": PhotoType.FRONT}],
        )
        metas = load_photo_metadata(session, item)

    def fake_path_cls(path_str):
        class _MockPath:
            suffix = ".jpg"

            def __init__(self, value):
                self._value = value
                self.stem = value.split(".")[0]

            def exists(self):
                return True

            def read_bytes(self):
                return b"IMG"

        return _MockPath(path_str)

    monkeypatch.setattr("packages.intake.src.providers.claude_deep_analysis.Path", fake_path_cls)
    provider = ClaudeDeepAnalysisProvider(_Settings())
    result = provider._select_category_images(item.image_paths, metas, "books")

    assert result.used_paths.index("img001.jpg") < result.used_paths.index("spine.jpg")
