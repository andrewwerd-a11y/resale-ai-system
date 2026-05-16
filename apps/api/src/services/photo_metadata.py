"""Photo metadata resolution and local persistence helpers."""
from __future__ import annotations

from dataclasses import asdict

from sqlmodel import Session

from packages.data.src.repositories.item_photo_metadata_repo import ItemPhotoMetadataRepository
from packages.domain.src.entities.item import Item
from packages.intake.src.photo_types import PhotoMeta, parse_photo_inputs
from packages.intake.src.pipeline_types import PhotoLabelSource, PhotoSource, PhotoType


def load_photo_metadata(session: Session, item: Item) -> list[PhotoMeta]:
    repo = ItemPhotoMetadataRepository(session)
    records = repo.list_for_sku(str(item.sku or ""))
    explicit = [
        PhotoMeta(
            path=record.image_path,
            photo_type=record.photo_type or PhotoType.UNKNOWN,
            source=_path_source_for_path(record.image_path),
            label_source=record.label_source or PhotoLabelSource.UNKNOWN,
            confidence=float(record.confidence or 0.0),
            user_labeled=(record.label_source == PhotoLabelSource.USER_LABELED),
            model_labeled=(record.label_source == PhotoLabelSource.MODEL_LABELED),
            is_cover=bool(record.is_cover),
            sort_order=record.sort_order,
            notes=record.notes,
            created_at=record.created_at.isoformat() if record.created_at else None,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
        )
        for record in records
    ]
    metas = parse_photo_inputs(item, explicit_meta=explicit)
    index_by_path = {str(path): idx for idx, path in enumerate(item.image_paths or [])}
    for meta in metas:
        if meta.sort_order is None:
            meta.sort_order = index_by_path.get(meta.path)
        if not meta.is_cover:
            meta.is_cover = (meta.sort_order == 0)
    return sorted(
        metas,
        key=lambda meta: (
            1 if meta.sort_order is None else 0,
            meta.sort_order if meta.sort_order is not None else 9999,
            meta.path,
        ),
    )


def upsert_photo_labels(
    session: Session,
    item: Item,
    updates: list[dict],
) -> list[PhotoMeta]:
    repo = ItemPhotoMetadataRepository(session)
    index_by_path = {str(path): idx for idx, path in enumerate(item.image_paths or [])}
    for payload in updates:
        image_path = str(payload.get("image_path") or "").strip()
        if not image_path:
            continue
        sort_order = index_by_path.get(image_path)
        repo.upsert(
            sku=str(item.sku or ""),
            image_path=image_path,
            photo_type=str(payload.get("photo_type") or PhotoType.UNKNOWN),
            label_source=PhotoLabelSource.USER_LABELED,
            confidence=float(payload.get("confidence") or 1.0),
            is_cover=bool(sort_order == 0),
            sort_order=sort_order,
            notes=(str(payload.get("notes")) if payload.get("notes") is not None else None),
        )
    return load_photo_metadata(session, item)


def delete_photo_metadata_for_path(session: Session, *, sku: str, image_path: str) -> None:
    ItemPhotoMetadataRepository(session).delete_for_sku_and_path(sku, image_path)


def sync_photo_metadata_cover_order(session: Session, *, sku: str, ordered_paths: list[str]) -> None:
    ItemPhotoMetadataRepository(session).sync_cover_and_sort_order(sku, ordered_paths)


def photo_metadata_response(session: Session, item: Item) -> dict:
    metas = load_photo_metadata(session, item)
    return {
        "sku": item.sku,
        "photos": [photo_meta_to_api_dict(meta) for meta in metas],
        "local_only": True,
        "no_ebay_mutation_performed": True,
        "no_publish_performed": True,
        "manual_approval_required": True,
    }


def photo_meta_to_api_dict(meta: PhotoMeta) -> dict:
    data = asdict(meta)
    data["image_path"] = data.pop("path")
    return data


def photo_metadata_rollup(meta: list[PhotoMeta]) -> dict:
    user_labeled = [entry for entry in meta if entry.label_source == PhotoLabelSource.USER_LABELED]
    unlabeled = [entry for entry in meta if entry.photo_type == PhotoType.UNKNOWN]
    return {
        "labeled_photo_count": len([entry for entry in meta if entry.photo_type != PhotoType.UNKNOWN]),
        "unlabeled_photo_count": len(unlabeled),
        "user_labeled_photo_types": sorted({entry.photo_type for entry in user_labeled if entry.photo_type != PhotoType.UNKNOWN}),
        "photo_metadata_status": _photo_metadata_status(meta, user_labeled),
    }


def _photo_metadata_status(meta: list[PhotoMeta], user_labeled: list[PhotoMeta]) -> str:
    if not meta:
        return "no_photos"
    if not user_labeled:
        return "no_labels"
    if len(user_labeled) < len(meta):
        return "partial_labels"
    return "fully_labeled"


def _path_source_for_path(path: str) -> str:
    value = str(path or "")
    if value.startswith("http://") or value.startswith("https://"):
        return PhotoSource.HOSTED
    return PhotoSource.LOCAL
