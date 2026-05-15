"""Photo metadata sidecar layer.

Backward-compatible with `Item.image_paths: list[str]`. Provides an in-memory
typed view (`PhotoMeta`) plus helpers that future provider-labeled photo flows
can populate without changing the storage contract.

No DB migration here — sidecar lives on the request/preview boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from packages.domain.src.entities.item import Item
from packages.intake.src.pipeline_types import PhotoSource, PhotoType


# Mapping from internal photo_type tokens (also used by quality_gate.py) to
# the canonical PhotoType enum. quality_gate keeps its richer category-family
# tokens; this map is for cross-cutting summaries only.
QUALITY_GATE_TO_PHOTO_TYPE: dict[str, str] = {
    "front_cover": PhotoType.FRONT,
    "back_cover": PhotoType.BACK,
    "spine": PhotoType.SPINE,
    "title_page": PhotoType.TITLE_PAGE,
    "copyright_publication_page": PhotoType.COPYRIGHT_PAGE,
    "condition_flaws": PhotoType.FLAW,
    "markings_annotations": PhotoType.DETAIL,
    "front": PhotoType.FRONT,
    "back": PhotoType.BACK,
    "brand_tag": PhotoType.BRAND_TAG,
    "size_tag": PhotoType.SIZE_TAG,
    "material_care_tag": PhotoType.MATERIAL_CARE_TAG,
    "measurements": PhotoType.MEASUREMENT,
    "flaws_wear": PhotoType.FLAW,
    "pair_front_side": PhotoType.FRONT,
    "soles": PhotoType.SOLE,
    "size_tag_inside_label": PhotoType.SIZE_TAG,
    "brand_label": PhotoType.LABEL,
    "heels_toes_wear": PhotoType.FLAW,
    "material_detail": PhotoType.DETAIL,
    "tag_tush_tag": PhotoType.TUSH_TAG,
    "scale_measurement": PhotoType.SCALE,
    "defects_wear": PhotoType.FLAW,
    "copyright_manufacturer_tag": PhotoType.TAG,
    "front_back": PhotoType.FRONT,
    "interior": PhotoType.INTERIOR,
    "brand_logo": PhotoType.LABEL,
    "serial_date_code": PhotoType.SERIAL_OR_DATE_CODE,
    "hardware": PhotoType.HARDWARE,
    "corners_wear": PhotoType.CORNERS_WEAR,
    "strap_handle": PhotoType.DETAIL,
    "authenticity_sensitive_evidence": PhotoType.SERIAL_OR_DATE_CODE,
    "full_object": PhotoType.FRONT,
    "maker_marks": PhotoType.MAKER_MARK,
    "bottom_back": PhotoType.BACK,
    "close_ups": PhotoType.DETAIL,
    "defects": PhotoType.FLAW,
    "scale": PhotoType.SCALE,
    "provenance_context": PhotoType.DETAIL,
}


FILENAME_KEYWORDS: dict[str, list[str]] = {
    PhotoType.SPINE: ["spine"],
    PhotoType.TITLE_PAGE: ["title page", "title-page", "titlepage"],
    PhotoType.COPYRIGHT_PAGE: ["copyright", "publication"],
    PhotoType.BRAND_TAG: ["brand tag", "brand-tag", "brandtag"],
    PhotoType.SIZE_TAG: ["size tag", "size-tag", "sizetag"],
    PhotoType.MATERIAL_CARE_TAG: ["care tag", "care-tag", "material tag", "fabric tag"],
    PhotoType.TUSH_TAG: ["tush", "hangtag", "hang tag", "hang-tag"],
    PhotoType.SOLE: ["sole", "soles"],
    PhotoType.INTERIOR: ["interior", "inside", "lining"],
    PhotoType.HARDWARE: ["hardware", "zipper", "clasp", "buckle"],
    PhotoType.CORNERS_WEAR: ["corner"],
    PhotoType.SERIAL_OR_DATE_CODE: ["serial", "date code", "date-code", "datecode"],
    PhotoType.MAKER_MARK: ["maker", "mark", "stamp", "signature"],
    PhotoType.MEASUREMENT: ["measurement", "measure", "ruler", "tape"],
    PhotoType.SCALE: ["scale"],
    PhotoType.FLAW: ["flaw", "defect", "damage", "wear", "stain", "tear", "hole", "chip", "crack"],
    PhotoType.LABEL: ["label", "logo"],
    PhotoType.TAG: ["tag"],
    PhotoType.DETAIL: ["detail", "closeup", "close-up", "close up"],
    PhotoType.BACK: ["back"],
    PhotoType.SIDE: ["side"],
    PhotoType.FRONT: ["front"],
}


@dataclass
class PhotoMeta:
    path: str
    photo_type: str = PhotoType.UNKNOWN
    source: str = PhotoSource.LOCAL
    confidence: float = 0.0
    user_labeled: bool = False
    model_labeled: bool = False
    notes: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PhotoCoverageSummary:
    category_family: str
    total_photos: int
    present_photo_types: list[str] = field(default_factory=list)
    missing_required_photo_types: list[str] = field(default_factory=list)
    missing_recommended_photo_types: list[str] = field(default_factory=list)
    unknown_photo_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def infer_photo_type_from_filename(path: str) -> tuple[str, float]:
    """Infer a PhotoType from filename text alone.

    Returns (photo_type, confidence). Conservative: only returns non-UNKNOWN
    when a keyword cleanly matches. Confidence is low — this is a fallback.
    """
    if not path:
        return PhotoType.UNKNOWN, 0.0
    raw = str(path).lower()
    stem = Path(raw).stem.lower()
    haystack = f"{raw} {stem}".replace("_", " ").replace("-", " ")
    # Check multi-word keywords first (more specific) by iterating in declared order.
    for photo_type, keywords in FILENAME_KEYWORDS.items():
        for keyword in keywords:
            normalized = keyword.replace("_", " ").replace("-", " ")
            if normalized in haystack:
                return photo_type, 0.45
    return PhotoType.UNKNOWN, 0.0


def parse_photo_inputs(
    item: Item,
    explicit_meta: Iterable[PhotoMeta | dict] | None = None,
) -> list[PhotoMeta]:
    """Derive a PhotoMeta list for an item.

    - If `explicit_meta` is supplied, those entries win (caller-provided labels).
    - Any remaining `item.image_paths` not covered by explicit_meta get a
      filename-inferred PhotoMeta with model_labeled=True (filename heuristic).
    """
    explicit_by_path: dict[str, PhotoMeta] = {}
    for entry in explicit_meta or []:
        if isinstance(entry, PhotoMeta):
            explicit_by_path[entry.path] = entry
        elif isinstance(entry, dict) and entry.get("path"):
            explicit_by_path[entry["path"]] = PhotoMeta(**{
                k: v for k, v in entry.items() if k in PhotoMeta.__dataclass_fields__
            })

    out: list[PhotoMeta] = []
    seen_paths: set[str] = set()
    for path in item.image_paths or []:
        path_str = str(path)
        seen_paths.add(path_str)
        if path_str in explicit_by_path:
            out.append(explicit_by_path[path_str])
            continue
        photo_type, confidence = infer_photo_type_from_filename(path_str)
        out.append(
            PhotoMeta(
                path=path_str,
                photo_type=photo_type,
                source=PhotoSource.LOCAL,
                confidence=confidence,
                user_labeled=False,
                model_labeled=photo_type != PhotoType.UNKNOWN,
                created_at=datetime.utcnow().isoformat() if photo_type != PhotoType.UNKNOWN else None,
            )
        )
    # Explicit-only entries (no matching image_path) — preserve them too.
    for path, meta in explicit_by_path.items():
        if path not in seen_paths:
            out.append(meta)
    return out


def merge_user_photo_labels(
    existing: Iterable[PhotoMeta],
    new_labels: Iterable[PhotoMeta | dict],
) -> list[PhotoMeta]:
    """Apply user-provided photo labels on top of existing metadata.

    User labels always win over inferred labels and are marked user_labeled=True.
    """
    by_path: dict[str, PhotoMeta] = {m.path: m for m in existing}
    for entry in new_labels or []:
        if isinstance(entry, dict):
            path = entry.get("path")
            if not path:
                continue
            photo_type = entry.get("photo_type", PhotoType.UNKNOWN)
            confidence = float(entry.get("confidence", 1.0))
            notes = entry.get("notes")
        else:
            path = entry.path
            photo_type = entry.photo_type
            confidence = entry.confidence or 1.0
            notes = entry.notes
        previous = by_path.get(path)
        merged = PhotoMeta(
            path=path,
            photo_type=photo_type,
            source=previous.source if previous else PhotoSource.LOCAL,
            confidence=confidence,
            user_labeled=True,
            model_labeled=previous.model_labeled if previous else False,
            notes=notes if notes is not None else (previous.notes if previous else None),
            created_at=(previous.created_at if previous else datetime.utcnow().isoformat()),
        )
        by_path[path] = merged
    return list(by_path.values())


def summarize_photo_coverage(
    item: Item,
    category_family: str,
    photo_meta: Iterable[PhotoMeta] | None = None,
) -> PhotoCoverageSummary:
    """Summarize coverage across a category family's required photo set.

    Uses the existing quality_gate PHOTO_REQUIREMENTS so this stays the single
    source of truth for "what does this family need?". Returns mapped enum
    photo_type labels; recommended/optional types are derived from quality_gate's
    OPTIONAL_WHEN_NOT_PRESENT.
    """
    # Local import to avoid circulars.
    from packages.intake.src.quality_gate import (
        OPTIONAL_WHEN_NOT_PRESENT,
        PHOTO_REQUIREMENTS,
        PHOTO_TYPE_LABELS,
        infer_present_photo_types,
    )

    required_tokens = PHOTO_REQUIREMENTS.get(category_family, [])
    metas = list(photo_meta) if photo_meta is not None else parse_photo_inputs(item)
    present_tokens = infer_present_photo_types(item, photo_meta=metas)

    # Required = required and not optional, Recommended = optional set members.
    missing_required: list[str] = []
    missing_recommended: list[str] = []
    for token in required_tokens:
        if token in present_tokens:
            continue
        label = PHOTO_TYPE_LABELS.get(token, token)
        if token in OPTIONAL_WHEN_NOT_PRESENT:
            missing_recommended.append(label)
        else:
            missing_required.append(label)

    present_labels = sorted(
        PHOTO_TYPE_LABELS.get(token, token) for token in present_tokens
    )
    unknown_count = sum(1 for m in metas if m.photo_type == PhotoType.UNKNOWN)
    return PhotoCoverageSummary(
        category_family=category_family,
        total_photos=len(metas),
        present_photo_types=present_labels,
        missing_required_photo_types=missing_required,
        missing_recommended_photo_types=missing_recommended,
        unknown_photo_count=unknown_count,
    )


def missing_photo_types_for_category(
    item: Item,
    category_family: str,
) -> list[str]:
    """Convenience: just the required-but-missing photo type human labels."""
    return summarize_photo_coverage(item, category_family).missing_required_photo_types
