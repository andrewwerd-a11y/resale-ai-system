"""Photo metadata resolution and local persistence helpers."""
from __future__ import annotations

from dataclasses import asdict

from sqlmodel import Session

from packages.data.src.repositories.item_photo_metadata_repo import ItemPhotoMetadataRepository
from packages.domain.src.entities.item import Item
from packages.intake.src.photo_types import PhotoMeta, parse_photo_inputs
from packages.intake.src.pipeline_types import PhotoLabelSource, PhotoSource, PhotoType
from packages.intake.src.quality_gate import category_family_for_item

ALL_PHOTO_TYPE_OPTIONS = [
    {"value": PhotoType.FRONT, "label": "Front cover"},
    {"value": PhotoType.BACK, "label": "Back cover"},
    {"value": PhotoType.SPINE, "label": "Spine"},
    {"value": PhotoType.TITLE_PAGE, "label": "Title page"},
    {"value": PhotoType.COPYRIGHT_PAGE, "label": "Copyright/publication page"},
    {"value": PhotoType.FLAW, "label": "Condition/flaws"},
    {"value": PhotoType.BRAND_TAG, "label": "Brand tag"},
    {"value": PhotoType.SIZE_TAG, "label": "Size tag"},
    {"value": PhotoType.MATERIAL_CARE_TAG, "label": "Material/care tag"},
    {"value": PhotoType.TUSH_TAG, "label": "Tag/tush tag"},
    {"value": PhotoType.MEASUREMENT, "label": "Measurement"},
    {"value": PhotoType.SCALE, "label": "Scale/measurement"},
    {"value": PhotoType.LABEL, "label": "Label/logo"},
    {"value": PhotoType.TAG, "label": "Tag"},
    {"value": PhotoType.DETAIL, "label": "Detail"},
    {"value": PhotoType.INTERIOR, "label": "Interior"},
    {"value": PhotoType.SOLE, "label": "Soles"},
    {"value": PhotoType.HARDWARE, "label": "Hardware"},
    {"value": PhotoType.CORNERS_WEAR, "label": "Corners/wear"},
    {"value": PhotoType.SERIAL_OR_DATE_CODE, "label": "Serial/date code"},
    {"value": PhotoType.MAKER_MARK, "label": "Maker mark"},
    {"value": PhotoType.SIDE, "label": "Side"},
    {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
]

COMPACT_GENERIC_PHOTO_TYPE_OPTIONS = [
    {"value": PhotoType.FRONT, "label": "Front / full object"},
    {"value": PhotoType.BACK, "label": "Back / underside"},
    {"value": PhotoType.DETAIL, "label": "Detail / close-up"},
    {"value": PhotoType.FLAW, "label": "Condition / defects"},
    {"value": PhotoType.MEASUREMENT, "label": "Measurement"},
    {"value": PhotoType.LABEL, "label": "Label / maker mark"},
    {"value": PhotoType.TAG, "label": "Tag / code"},
    {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
]

CATEGORY_PHOTO_TYPE_OPTIONS = {
    "books": [
        {"value": PhotoType.FRONT, "label": "Front cover"},
        {"value": PhotoType.BACK, "label": "Back cover"},
        {"value": PhotoType.SPINE, "label": "Spine"},
        {"value": PhotoType.TITLE_PAGE, "label": "Title page"},
        {"value": PhotoType.COPYRIGHT_PAGE, "label": "Copyright/publication page"},
        {"value": PhotoType.FLAW, "label": "Condition/flaws"},
        {"value": PhotoType.DETAIL, "label": "Markings/annotations"},
        {"value": PhotoType.DETAIL, "label": "Interior/sample page"},
        {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
    ],
    "clothing": [
        {"value": PhotoType.FRONT, "label": "Front"},
        {"value": PhotoType.BACK, "label": "Back"},
        {"value": PhotoType.BRAND_TAG, "label": "Brand tag"},
        {"value": PhotoType.SIZE_TAG, "label": "Size tag"},
        {"value": PhotoType.MATERIAL_CARE_TAG, "label": "Material/care tag"},
        {"value": PhotoType.MEASUREMENT, "label": "Measurements"},
        {"value": PhotoType.FLAW, "label": "Flaws/wear"},
        {"value": PhotoType.DETAIL, "label": "Detail"},
        {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
    ],
    "plush_toys": [
        {"value": PhotoType.FRONT, "label": "Front"},
        {"value": PhotoType.BACK, "label": "Back"},
        {"value": PhotoType.TUSH_TAG, "label": "Tag/tush tag"},
        {"value": PhotoType.SCALE, "label": "Scale/measurement"},
        {"value": PhotoType.FLAW, "label": "Defects/wear"},
        {"value": PhotoType.TAG, "label": "Copyright/manufacturer tag"},
        {"value": PhotoType.DETAIL, "label": "Detail"},
        {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
    ],
    "bags": [
        {"value": PhotoType.FRONT, "label": "Front/back"},
        {"value": PhotoType.INTERIOR, "label": "Interior"},
        {"value": PhotoType.LABEL, "label": "Brand/logo"},
        {"value": PhotoType.SERIAL_OR_DATE_CODE, "label": "Serial/date code"},
        {"value": PhotoType.HARDWARE, "label": "Hardware"},
        {"value": PhotoType.CORNERS_WEAR, "label": "Corners/wear"},
        {"value": PhotoType.DETAIL, "label": "Strap/handle"},
        {"value": PhotoType.SERIAL_OR_DATE_CODE, "label": "Authenticity evidence"},
        {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
    ],
    "collectibles_antiques": [
        {"value": PhotoType.FRONT, "label": "Full object"},
        {"value": PhotoType.MAKER_MARK, "label": "Maker marks"},
        {"value": PhotoType.BACK, "label": "Bottom/back"},
        {"value": PhotoType.DETAIL, "label": "Close-ups"},
        {"value": PhotoType.FLAW, "label": "Defects"},
        {"value": PhotoType.SCALE, "label": "Scale"},
        {"value": PhotoType.DETAIL, "label": "Provenance/context"},
        {"value": PhotoType.UNKNOWN, "label": "Unknown / unlabeled"},
    ],
}

PHOTO_TYPE_ALIASES = {
    "front cover": PhotoType.FRONT,
    "front_cover": PhotoType.FRONT,
    "back cover": PhotoType.BACK,
    "back_cover": PhotoType.BACK,
    "title page": PhotoType.TITLE_PAGE,
    "title_page": PhotoType.TITLE_PAGE,
    "copyright/publication page": PhotoType.COPYRIGHT_PAGE,
    "copyright publication page": PhotoType.COPYRIGHT_PAGE,
    "copyright_publication_page": PhotoType.COPYRIGHT_PAGE,
    "condition/flaws": PhotoType.FLAW,
    "condition flaws": PhotoType.FLAW,
    "condition_flaws": PhotoType.FLAW,
    "flaws/wear": PhotoType.FLAW,
    "flaws wear": PhotoType.FLAW,
    "flaws_wear": PhotoType.FLAW,
    "brand tag": PhotoType.BRAND_TAG,
    "size tag": PhotoType.SIZE_TAG,
    "material/care tag": PhotoType.MATERIAL_CARE_TAG,
    "material care tag": PhotoType.MATERIAL_CARE_TAG,
    "measurements": PhotoType.MEASUREMENT,
    "measurement": PhotoType.MEASUREMENT,
    "tag/tush tag": PhotoType.TUSH_TAG,
    "tag tush tag": PhotoType.TUSH_TAG,
    "markings/annotations": PhotoType.DETAIL,
    "markings annotations": PhotoType.DETAIL,
    "interior/sample page": PhotoType.DETAIL,
    "interior sample page": PhotoType.DETAIL,
    "copyright/manufacturer tag": PhotoType.TAG,
    "copyright manufacturer tag": PhotoType.TAG,
    "authenticity evidence": PhotoType.SERIAL_OR_DATE_CODE,
    "front/back": PhotoType.FRONT,
    "full object": PhotoType.FRONT,
    "provenance/context": PhotoType.DETAIL,
    "provenance context": PhotoType.DETAIL,
    "brand/logo": PhotoType.LABEL,
    "brand logo": PhotoType.LABEL,
    "strap/handle": PhotoType.DETAIL,
    "strap handle": PhotoType.DETAIL,
    "scale/measurement": PhotoType.SCALE,
    "scale measurement": PhotoType.SCALE,
    "scale_measurement": PhotoType.SCALE,
}


def photo_type_options() -> list[dict]:
    return [dict(option) for option in ALL_PHOTO_TYPE_OPTIONS]


def photo_type_options_for_item(item: Item | None) -> tuple[str, list[dict]]:
    family = category_family_for_item(item) if item is not None else "unknown"
    options = CATEGORY_PHOTO_TYPE_OPTIONS.get(family, COMPACT_GENERIC_PHOTO_TYPE_OPTIONS)
    return family, [dict(option) for option in options]


def photo_type_option_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for option in ALL_PHOTO_TYPE_OPTIONS:
        labels.setdefault(str(option["value"]), str(option["label"]))
    return labels


def valid_photo_type_hint() -> str:
    return ", ".join(f"{option['value']} ({option['label']})" for option in ALL_PHOTO_TYPE_OPTIONS)


def normalize_photo_type_input(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw in PhotoType.ALL:
        return raw
    folded = raw.lower().replace("-", " ").replace("_", " ")
    if folded in PHOTO_TYPE_ALIASES:
        return PHOTO_TYPE_ALIASES[folded]
    return None


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
    category_family, options = photo_type_options_for_item(item)
    return {
        "sku": item.sku,
        "category_family": category_family,
        "photos": [photo_meta_to_api_dict(meta) for meta in metas],
        "photo_type_options": options,
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
