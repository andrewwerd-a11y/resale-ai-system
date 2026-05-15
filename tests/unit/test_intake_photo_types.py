from __future__ import annotations

from packages.domain.src.entities.item import Item
from packages.intake.src.photo_types import (
    PhotoMeta,
    infer_photo_type_from_filename,
    merge_user_photo_labels,
    missing_photo_types_for_category,
    parse_photo_inputs,
    summarize_photo_coverage,
)
from packages.intake.src.pipeline_types import PhotoSource, PhotoType


def _item(**overrides) -> Item:
    base = dict(
        sku="BK-PHOTO",
        category_key="books",
        category_label="Books",
        title_final="A Book",
        image_paths=[],
    )
    base.update(overrides)
    return Item(**base)


def test_infer_photo_type_recognizes_spine_filename():
    photo_type, conf = infer_photo_type_from_filename("BK-1/spine.jpg")
    assert photo_type == PhotoType.SPINE
    assert conf > 0


def test_infer_photo_type_unknown_for_anonymous_filename():
    photo_type, conf = infer_photo_type_from_filename("BK-1/01.jpg")
    assert photo_type == PhotoType.UNKNOWN
    assert conf == 0.0


def test_parse_photo_inputs_uses_filename_inference_by_default():
    item = _item(image_paths=["spine.jpg", "01.jpg", "back-cover.jpg"])
    metas = parse_photo_inputs(item)
    by_path = {m.path: m for m in metas}
    assert by_path["spine.jpg"].photo_type == PhotoType.SPINE
    assert by_path["spine.jpg"].model_labeled is True
    assert by_path["01.jpg"].photo_type == PhotoType.UNKNOWN
    assert by_path["01.jpg"].model_labeled is False
    assert by_path["back-cover.jpg"].photo_type == PhotoType.BACK


def test_parse_photo_inputs_respects_explicit_meta():
    item = _item(image_paths=["01.jpg"])
    explicit = [PhotoMeta(path="01.jpg", photo_type=PhotoType.FRONT,
                          user_labeled=True, confidence=1.0)]
    metas = parse_photo_inputs(item, explicit_meta=explicit)
    assert len(metas) == 1
    assert metas[0].photo_type == PhotoType.FRONT
    assert metas[0].user_labeled is True


def test_merge_user_photo_labels_overrides_inferred():
    existing = [PhotoMeta(path="01.jpg", photo_type=PhotoType.UNKNOWN,
                          model_labeled=False)]
    merged = merge_user_photo_labels(
        existing,
        [{"path": "01.jpg", "photo_type": PhotoType.BRAND_TAG}],
    )
    assert merged[0].photo_type == PhotoType.BRAND_TAG
    assert merged[0].user_labeled is True


def test_summarize_photo_coverage_books_missing_required():
    item = _item(image_paths=["front-cover.jpg", "back-cover.jpg", "spine.jpg"])
    summary = summarize_photo_coverage(item, "books")
    assert summary.category_family == "books"
    assert summary.total_photos == 3
    assert "title page" in summary.missing_required_photo_types
    assert "copyright/publication page" in summary.missing_required_photo_types


def test_missing_photo_types_for_category_returns_human_labels():
    item = _item(image_paths=["front-cover.jpg"])
    missing = missing_photo_types_for_category(item, "books")
    assert isinstance(missing, list)
    assert all(isinstance(label, str) for label in missing)
    assert "spine" in missing


def test_summarize_photo_coverage_unknown_family_has_no_requirements():
    item = _item(category_key="mystery", image_paths=["a.jpg"])
    summary = summarize_photo_coverage(item, "unknown")
    assert summary.missing_required_photo_types == []
    assert summary.missing_recommended_photo_types == []
