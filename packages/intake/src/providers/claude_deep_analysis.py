"""Claude deep-analysis provider for the staged intake pipeline.

DISABLED BY DEFAULT. Requires:
  INTAKE_EXTERNAL_PROVIDER_ENABLED=true
  INTAKE_PROVIDER=claude
  ANTHROPIC_API_KEY=<key>

When disabled or misconfigured, callers fall back to
DeterministicDeepAnalysisProvider transparently. This module never raises;
unavailability is communicated via is_available() and get_readiness().

Image support: local file paths are base64-encoded and sent to Claude Vision
using a category-aware priority ordering. Hosted URLs are skipped — no
external fetches. When no readable local photos are found, analysis is
text-only from item fields.

Output contract:
- should_require_manual_review is ALWAYS forced True regardless of model response.
- should_block_publish_approval is computed from risk flags, never from model.
- NEVER_AUTO_OVERWRITE fields are stripped from suggested_field_updates.
- publish_allowed is never set; that decision stays with human approval.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.intake.src.analysis_contract import DeepAnalysisRequest, DeepAnalysisResult
from packages.intake.src.correction_pipeline import NEVER_AUTO_OVERWRITE
from packages.intake.src.pipeline_types import (
    DETERMINISTIC_FALLBACK_WARNING,
    ConfidenceSource,
    ProviderKind,
    RiskFlag,
)

logger = logging.getLogger(__name__)

# Fields Claude must never guess — mirrored from DeterministicDeepAnalysisProvider.
_DO_NOT_GUESS_FIELDS = {"authenticity", "first_edition", "signed_by"}

# Fields Claude may suggest updates for (must not be in NEVER_AUTO_OVERWRITE).
_UPDATABLE_FIELDS = (
    "color", "material", "size", "type", "subcategory",
    "department", "format", "language", "style",
)

# Input/output token pricing for claude-sonnet-4 (per token).
_INPUT_PRICE_PER_TOKEN = 3.00 / 1_000_000
_OUTPUT_PRICE_PER_TOKEN = 15.00 / 1_000_000

# Category-aware photo priority lists (quality_gate token names, in preference order).
# Photos earlier in the list are more critical to the analysis.
_CATEGORY_PHOTO_PRIORITY: dict[str, list[str]] = {
    "books": [
        "front_cover", "back_cover", "spine", "title_page",
        "copyright_publication_page", "condition_flaws", "markings_annotations",
    ],
    "clothing": [
        "front", "back", "brand_tag", "size_tag",
        "material_care_tag", "measurements", "flaws_wear",
    ],
    "bags": [
        "front_back", "interior", "brand_logo", "serial_date_code",
        "hardware", "corners_wear", "strap_handle", "authenticity_sensitive_evidence",
    ],
    "plush_toys": [
        "front", "back", "tag_tush_tag", "scale_measurement",
        "defects_wear", "copyright_manufacturer_tag",
    ],
    "shoes": [
        "pair_front_side", "soles", "size_tag_inside_label",
        "brand_label", "heels_toes_wear", "material_detail",
    ],
    "collectibles_antiques": [
        "full_object", "maker_marks", "bottom_back",
        "close_ups", "defects", "scale", "provenance_context",
    ],
}

# Quality_gate token → filename keywords (inverse lookup for label inference).
_TOKEN_FILENAME_KEYWORDS: dict[str, list[str]] = {
    "front_cover": ["front", "front-cover", "front_cover"],
    "back_cover": ["back", "back-cover", "back_cover"],
    "spine": ["spine"],
    "title_page": ["title", "title-page", "titlepage"],
    "copyright_publication_page": ["copyright", "publication"],
    "condition_flaws": ["flaw", "condition", "damage", "wear"],
    "markings_annotations": ["mark", "annotation", "written", "inscription"],
    "front": ["front"],
    "back": ["back"],
    "brand_tag": ["brand", "brand-tag", "brandtag"],
    "size_tag": ["size", "size-tag", "sizetag"],
    "material_care_tag": ["care", "material", "fabric"],
    "measurements": ["measurement", "measure", "ruler"],
    "flaws_wear": ["flaw", "wear", "damage"],
    "pair_front_side": ["pair", "side"],
    "soles": ["sole", "soles"],
    "size_tag_inside_label": ["size", "inside", "label"],
    "brand_label": ["brand", "label"],
    "heels_toes_wear": ["heel", "toe", "wear"],
    "material_detail": ["material", "texture", "detail"],
    "tag_tush_tag": ["tush", "hangtag", "hang-tag"],
    "scale_measurement": ["scale", "measurement"],
    "defects_wear": ["defect", "flaw", "wear"],
    "copyright_manufacturer_tag": ["copyright", "manufacturer"],
    "front_back": ["front", "back"],
    "interior": ["interior", "inside", "lining"],
    "brand_logo": ["brand", "logo"],
    "serial_date_code": ["serial", "date-code", "datecode"],
    "hardware": ["hardware", "zipper", "clasp", "buckle"],
    "corners_wear": ["corner"],
    "strap_handle": ["strap", "handle"],
    "authenticity_sensitive_evidence": ["auth", "authenticity", "certificate"],
    "full_object": ["full", "object", "overview"],
    "maker_marks": ["maker", "mark", "stamp"],
    "bottom_back": ["bottom", "base"],
    "close_ups": ["closeup", "close-up", "detail"],
    "defects": ["defect", "flaw", "damage"],
    "scale": ["scale", "ruler"],
    "provenance_context": ["provenance", "receipt", "cert"],
}


def _infer_token_for_path(path_str: str, category_family: str) -> str | None:
    """Guess a quality_gate token from a filename using keyword matching."""
    stem = Path(path_str).stem.lower().replace("-", " ").replace("_", " ")
    priority = _CATEGORY_PHOTO_PRIORITY.get(category_family, [])
    for token in priority:
        keywords = _TOKEN_FILENAME_KEYWORDS.get(token, [])
        if any(kw in stem for kw in keywords):
            return token
    return None


@dataclass
class _ImageSelectionResult:
    """Outcome of category-aware image selection."""
    image_blocks: list[dict]
    used_paths: list[str]
    selected_photo_types: list[str]
    skipped_paths: list[str]
    skipped_reasons: list[str]
    required_missing: list[str]

_SYSTEM_PROMPT = """\
You are an expert resale item analyst assisting a human reviewer. You will receive
structured data about an item under review for a resale marketplace listing.

CRITICAL RULES — enforce exactly:
1. Never invent or fabricate field values. If evidence is absent, mark the field
   in uncertain_fields.
2. Never claim authenticity (authentic, genuine, first edition, signed) without
   strong photographic evidence in the provided photos.
3. Always return should_require_manual_review as true — this is a draft for human review.
4. Never suggest the item is publish-approved or ready to list.
5. When photo evidence is insufficient for a conclusion, set needs_more_photos true
   and list missing_photo_types.
6. Do not guess brand, condition_id, estimated_price, or ebay_category_id unless
   evidence is strong and explicitly visible.
7. Return ONLY valid JSON matching the output schema. No markdown, no commentary.
"""

_OUTPUT_SCHEMA = {
    "suggested_field_updates": {
        "__doc__": "Only fields you are confident about. No NEVER_AUTO_OVERWRITE fields.",
        "color": "string or null",
        "material": "string or null",
        "size": "string or null",
        "type": "string or null",
        "subcategory": "string or null",
        "department": "string or null",
        "format": "string or null",
        "language": "string or null",
    },
    "confidence_by_field": {
        "__doc__": "0.0-1.0 per field in suggested_field_updates"
    },
    "evidence_by_field": {
        "__doc__": "List of evidence strings per field"
    },
    "uncertain_fields": ["list of field names with insufficient evidence"],
    "do_not_guess_fields": ["authenticity", "first_edition", "signed_by"],
    "suggested_condition_id": "numeric string like '5000', or null",
    "condition_assessment": "brief condition description",
    "item_specifics": {"key": "value"},
    "title_suggestions": ["optimized title under 80 chars"],
    "description_suggestion": "3-4 sentence listing description",
    "pricing_estimate": 0.00,
    "needs_more_photos": False,
    "missing_photo_types": [],
    "publish_risk_flags": [
        "list of risk flag strings — valid values: missing_required_photos, "
        "malformed_condition_id, marketplace_policy_unknown, high_value_estimate, "
        "authenticity_sensitive_brand, needs_manual_review"
    ],
    "correction_summary": ["bullet point corrections needed"],
    "should_require_manual_review": True,
    "analysis_notes": "any important observations",
}


def _is_anthropic_installed() -> bool:
    try:
        return importlib.util.find_spec("anthropic") is not None
    except Exception:
        return False


def _effective_model(settings) -> str:
    """Return the intake model, falling back to enrichment_model."""
    m = (getattr(settings, "intake_model", "") or "").strip()
    return m or (getattr(settings, "enrichment_model", "") or "claude-sonnet-4-20250514")


class ClaudeDeepAnalysisProvider:
    """Real Claude provider for deep-analysis preview.

    Disabled by default. Enabled when:
      settings.intake_external_provider_enabled is True
      settings.intake_provider == "claude"
      settings.anthropic_api_key is non-empty
      anthropic package is installed
    """

    name = "claude-intake"

    def __init__(self, settings=None) -> None:
        if settings is None:
            from packages.core.src.config import get_settings
            settings = get_settings()
        self._settings = settings

    def is_available(self) -> bool:
        s = self._settings
        return bool(
            getattr(s, "intake_external_provider_enabled", False)
            and getattr(s, "intake_provider", "deterministic") == "claude"
            and (getattr(s, "anthropic_api_key", "") or "").strip()
            and _is_anthropic_installed()
        )

    def get_readiness(self) -> dict:
        s = self._settings
        enabled = bool(getattr(s, "intake_external_provider_enabled", False))
        provider_set = getattr(s, "intake_provider", "deterministic") == "claude"
        api_key = bool((getattr(s, "anthropic_api_key", "") or "").strip())
        pkg = _is_anthropic_installed()
        model = _effective_model(s)

        if not enabled:
            return {
                "available": False,
                "code": "disabled",
                "message": "Intake external provider is disabled (INTAKE_EXTERNAL_PROVIDER_ENABLED=false).",
                "next_action": "Set INTAKE_EXTERNAL_PROVIDER_ENABLED=true and INTAKE_PROVIDER=claude to enable.",
            }
        if not provider_set:
            return {
                "available": False,
                "code": "provider_not_selected",
                "message": "INTAKE_PROVIDER is not set to 'claude'.",
                "next_action": "Set INTAKE_PROVIDER=claude in your environment.",
            }
        if not api_key:
            return {
                "available": False,
                "code": "missing_api_key",
                "message": "ANTHROPIC_API_KEY is not configured.",
                "next_action": "Add ANTHROPIC_API_KEY to your environment before enabling the Claude provider.",
            }
        if not pkg:
            return {
                "available": False,
                "code": "package_not_installed",
                "message": "anthropic package is not installed.",
                "next_action": "Run: uv sync (anthropic is in dev dependencies).",
            }
        return {
            "available": True,
            "code": "ready",
            "message": f"Claude intake provider ready ({model}).",
            "model": model,
        }

    # ── Image helpers ──────────────────────────────────────────────────────────

    def _max_images_for_family(self, category_family: str) -> int:
        s = self._settings
        mapping = {
            "books": getattr(s, "intake_max_images_books", 6),
            "clothing": getattr(s, "intake_max_images_clothing", 6),
            "bags": getattr(s, "intake_max_images_bags", 7),
            "plush_toys": getattr(s, "intake_max_images_toys", 5),
            "shoes": getattr(s, "intake_max_images_default", 5),
            "collectibles_antiques": getattr(s, "intake_max_images_default", 5),
        }
        return mapping.get(category_family, getattr(s, "intake_max_images_default", 5))

    def _select_category_images(
        self,
        image_paths: list[str],
        photo_meta: list,
        category_family: str,
    ) -> _ImageSelectionResult:
        """Select images in category priority order, respecting byte and count caps.

        Priority: explicit PhotoMeta labels > filename keyword inference > order.
        Hosted URLs and unreadable files are skipped with a logged reason.
        """
        max_count = self._max_images_for_family(category_family)
        max_bytes = getattr(self._settings, "intake_max_image_bytes_total", 10 * 1024 * 1024)
        priority_tokens = _CATEGORY_PHOTO_PRIORITY.get(category_family, [])

        # Build a label map: path → quality_gate token from PhotoMeta (if labeled).
        # When multiple tokens share a PhotoType, prefer the first one in the
        # category priority list so books get "front_cover" not "front" for FRONT.
        from packages.intake.src.photo_types import QUALITY_GATE_TO_PHOTO_TYPE
        labeled: dict[str, str] = {}
        for pm in (photo_meta or []):
            pt = getattr(pm, "photo_type", None)
            path_key = getattr(pm, "path", None) or getattr(pm, "local_path", None)
            if pt and path_key:
                # Find the highest-priority token in this category that maps to pt.
                matched_token: str | None = None
                for tok in priority_tokens:
                    if QUALITY_GATE_TO_PHOTO_TYPE.get(tok) == pt:
                        matched_token = tok
                        break
                # Fall back to any token mapping if not in priority list.
                if matched_token is None:
                    for tok, mapped_pt in QUALITY_GATE_TO_PHOTO_TYPE.items():
                        if mapped_pt == pt:
                            matched_token = tok
                            break
                if matched_token:
                    labeled[str(path_key)] = matched_token

        # Sort paths: priority-ordered tokens first, then remaining by list order.
        def sort_key(p: str) -> tuple[int, int]:
            token = labeled.get(p) or _infer_token_for_path(p, category_family)
            if token and token in priority_tokens:
                return (0, priority_tokens.index(token))
            return (1, image_paths.index(p) if p in image_paths else 999)

        sorted_paths = sorted(image_paths, key=sort_key)

        blocks: list[dict] = []
        used_paths: list[str] = []
        selected_tokens: list[str] = []
        skipped_paths: list[str] = []
        skipped_reasons: list[str] = []
        total_bytes = 0

        for path_str in sorted_paths:
            if len(blocks) >= max_count:
                skipped_paths.append(path_str)
                skipped_reasons.append("count_cap")
                continue
            if path_str.startswith("http://") or path_str.startswith("https://"):
                skipped_paths.append(path_str)
                skipped_reasons.append("hosted_url_skipped")
                continue
            p = Path(path_str)
            if not p.exists():
                skipped_paths.append(path_str)
                skipped_reasons.append("file_not_found")
                continue
            try:
                raw_bytes = p.read_bytes()
            except Exception as exc:
                logger.warning("Could not read photo %s: %s", path_str, exc)
                skipped_paths.append(path_str)
                skipped_reasons.append("read_error")
                continue
            if total_bytes + len(raw_bytes) > max_bytes:
                skipped_paths.append(path_str)
                skipped_reasons.append("byte_cap")
                continue
            data = base64.standard_b64encode(raw_bytes).decode()
            ext = p.suffix.lower().lstrip(".")
            media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            })
            used_paths.append(path_str)
            token = labeled.get(path_str) or _infer_token_for_path(path_str, category_family)
            if token:
                selected_tokens.append(token)
            total_bytes += len(raw_bytes)

        # Identify required tokens missing from selected set.
        required_missing = [
            t for t in priority_tokens[:3]  # top 3 are always required
            if t not in selected_tokens
        ]

        return _ImageSelectionResult(
            image_blocks=blocks,
            used_paths=used_paths,
            selected_photo_types=selected_tokens,
            skipped_paths=skipped_paths,
            skipped_reasons=skipped_reasons,
            required_missing=required_missing,
        )

    # ── Prompt construction ────────────────────────────────────────────────────

    def _build_user_message(self, request: DeepAnalysisRequest) -> list[dict]:
        item = request.item
        item_data = {
            "sku": item.sku,
            "title_raw": item.title_raw,
            "title_final": item.title_final,
            "brand": item.brand,
            "model": getattr(item, "model", None),
            "color": item.color,
            "material": item.material,
            "size": item.size,
            "type": item.type,
            "subcategory": item.subcategory,
            "department": item.department,
            "condition_label": item.condition_label,
            "condition_id": item.condition_id,
            "condition_notes": item.condition_notes,
            "estimated_price": item.estimated_price,
            "category_key": item.category_key,
            "category_label": item.category_label,
            "ebay_category_name": item.ebay_category_name,
            "notes": item.notes,
            "image_count": len(item.image_paths or []),
        }

        pipeline_ctx = {
            "intake_quality_status": request.current_intake_quality_status,
            "required_aspects": request.required_aspects[:10],
            "recommended_aspects": request.recommended_aspects[:10],
            "allowed_condition_ids": request.allowed_condition_ids[:20],
            "current_publish_blockers": request.current_publish_blockers,
            "do_not_guess_policy": request.do_not_guess_policy,
            "do_not_guess_fields": sorted(_DO_NOT_GUESS_FIELDS),
            "never_auto_overwrite_fields": sorted(NEVER_AUTO_OVERWRITE),
        }

        if request.identity:
            pipeline_ctx["identity_decision"] = request.identity.decision
            pipeline_ctx["identity_uncertain_fields"] = request.identity.uncertain_fields

        if request.selected_category:
            pipeline_ctx["selected_category"] = {
                "platform": request.selected_category.platform,
                "category_id": request.selected_category.category_id,
                "category_name": request.selected_category.category_name,
                "confidence": request.selected_category.confidence,
                "condition_policy_known": request.selected_category.condition_policy_known,
            }

        if request.marketplace_requirements:
            mr = request.marketplace_requirements
            pipeline_ctx["marketplace_requires_live_fetch"] = mr.requires_live_read_only_fetch
            pipeline_ctx["missing_requirements_for_item"] = mr.missing_requirements_for_item[:10]

        if request.user_context:
            pipeline_ctx["user_context"] = request.user_context

        text_content = (
            f"ITEM DATA:\n{json.dumps(item_data, indent=2, default=str)}\n\n"
            f"PIPELINE CONTEXT:\n{json.dumps(pipeline_ctx, indent=2, default=str)}\n\n"
            f"OUTPUT SCHEMA (return ONLY valid JSON matching this shape):\n"
            f"{json.dumps(_OUTPUT_SCHEMA, indent=2, default=str)}"
        )
        return [{"type": "text", "text": text_content}]

    # ── Response parsing ───────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = []
            for line in lines[1:]:
                if line.strip() == "```":
                    break
                inner.append(line)
            text = "\n".join(inner)
        return json.loads(text)

    def _map_to_result(
        self,
        parsed: dict[str, Any],
        request: DeepAnalysisRequest,
        *,
        images_sent: int,
        input_tokens: int,
        output_tokens: int,
        selection: _ImageSelectionResult | None = None,
    ) -> DeepAnalysisResult:
        item = request.item

        # Suggested field updates — strip NEVER_AUTO_OVERWRITE.
        raw_updates: dict[str, Any] = parsed.get("suggested_field_updates") or {}
        suggestions: dict[str, Any] = {
            k: v for k, v in raw_updates.items()
            if k not in NEVER_AUTO_OVERWRITE and v is not None
        }
        confidence: dict[str, float] = {
            k: float(min(max(v, 0.0), 1.0))
            for k, v in (parsed.get("confidence_by_field") or {}).items()
            if k in suggestions
        }
        evidence: dict[str, list[str]] = {
            k: list(v) if isinstance(v, list) else [str(v)]
            for k, v in (parsed.get("evidence_by_field") or {}).items()
            if k in suggestions
        }
        uncertain: list[str] = list(parsed.get("uncertain_fields") or [])

        # Risk flags — validate against known tokens; discard unknowns.
        known_flags = set(RiskFlag.ALL)
        raw_flags = list(parsed.get("publish_risk_flags") or [])
        # Strip __doc__ sentinel if schema leaked through.
        raw_flags = [f for f in raw_flags if not str(f).startswith("__")]
        publish_risk_flags = [f for f in raw_flags if f in known_flags]
        # Carry through current publish blockers.
        for blocker in request.current_publish_blockers:
            if blocker not in publish_risk_flags:
                publish_risk_flags.append(blocker)

        authenticity_flags: list[str] = []
        high_value_flags: list[str] = []
        pricing_est = parsed.get("pricing_estimate")
        if pricing_est is not None:
            try:
                pricing_est = float(pricing_est)
            except (TypeError, ValueError):
                pricing_est = None
        if (pricing_est or 0) >= 75 or (item.estimated_price or 0) >= 75:
            high_value_flags.append(RiskFlag.HIGH_VALUE_ESTIMATE)
        text_blob = " ".join(str(v or "").lower() for v in [
            item.title_final, item.title_raw, item.brand, item.notes
        ])
        if any(t in text_blob for t in ["coach", "gucci", "prada", "louis vuitton"]):
            authenticity_flags.append(RiskFlag.AUTHENTICITY_SENSITIVE_BRAND)

        needs_more_photos = bool(parsed.get("needs_more_photos", False))
        # Also flag if required category photo types were missing from selected images.
        if selection and selection.required_missing and not needs_more_photos:
            needs_more_photos = True
        if needs_more_photos and RiskFlag.MISSING_REQUIRED_PHOTOS not in publish_risk_flags:
            publish_risk_flags.append(RiskFlag.MISSING_REQUIRED_PHOTOS)

        # should_require_manual_review is ALWAYS True — enforce regardless of model.
        should_block = bool(
            needs_more_photos
            or authenticity_flags
            or high_value_flags
            or request.current_publish_blockers
        )

        # Confidence source based on whether images were analyzed.
        conf_source = ConfidenceSource.VISUAL_MODEL if images_sent > 0 else ConfidenceSource.MIXED

        cost = round(
            input_tokens * _INPUT_PRICE_PER_TOKEN + output_tokens * _OUTPUT_PRICE_PER_TOKEN, 6
        )
        model_used = _effective_model(self._settings)

        correction_summary = list(parsed.get("correction_summary") or [])
        if not correction_summary and uncertain:
            correction_summary.append(
                "Uncertain fields require human confirmation: " + ", ".join(uncertain[:5])
            )

        # Merge model-reported missing types with selection-detected missing tokens.
        model_missing = list(parsed.get("missing_photo_types") or [])
        if selection:
            for t in selection.required_missing:
                if t not in model_missing:
                    model_missing.append(t)

        return DeepAnalysisResult(
            sku=request.sku or item.sku,
            suggested_field_updates=suggestions,
            confidence_by_field=confidence,
            evidence_by_field=evidence,
            uncertain_fields=uncertain,
            do_not_guess_fields=sorted(_DO_NOT_GUESS_FIELDS),
            suggested_condition_id=parsed.get("suggested_condition_id"),
            condition_assessment=parsed.get("condition_assessment") or item.condition_label,
            item_specifics=dict(parsed.get("item_specifics") or {}),
            title_suggestions=list(parsed.get("title_suggestions") or []),
            description_suggestion=parsed.get("description_suggestion"),
            pricing_estimate=pricing_est,
            pricing_evidence=[f"Claude estimate ({images_sent} images, {input_tokens}+{output_tokens} tokens, ~${cost})"],
            authenticity_flags=authenticity_flags,
            high_value_flags=high_value_flags,
            needs_more_photos=needs_more_photos,
            missing_photo_types=model_missing,
            publish_risk_flags=publish_risk_flags,
            correction_summary=correction_summary,
            should_require_manual_review=True,
            should_block_publish_approval=should_block,
            provider=self.name,
            provider_kind=ProviderKind.EXTERNAL_MODEL,
            confidence_source=conf_source,
            is_deterministic_fallback=False,
            fallback_warning="",
            selected_photo_types=selection.selected_photo_types if selection else [],
            selected_image_count=len(selection.image_blocks) if selection else images_sent,
            skipped_image_count=len(selection.skipped_paths) if selection else 0,
            skipped_image_reasons=selection.skipped_reasons if selection else [],
        )

    # ── Main entry point ───────────────────────────────────────────────────────

    def analyze(self, request: DeepAnalysisRequest) -> DeepAnalysisResult:
        import anthropic

        settings = self._settings
        model = _effective_model(settings)
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        # Resolve category family from item for image priority ordering.
        from packages.intake.src.quality_gate import category_family_for_item
        category_family = category_family_for_item(request.item)

        # Category-aware image selection.
        selection = self._select_category_images(
            list(request.item.image_paths or []),
            list(request.photo_meta or []),
            category_family,
        )
        images_sent = len(selection.image_blocks)

        text_parts = self._build_user_message(request)
        user_content = selection.image_blocks + text_parts  # images first, text last

        logger.info(
            "ClaudeDeepAnalysisProvider: sku=%s model=%s images=%d skipped=%d family=%s",
            request.sku, model, images_sent, len(selection.skipped_paths), category_family,
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            logger.error("Claude intake call failed for sku=%s: %s", request.sku, exc)
            raise

        raw = response.content[0].text
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        try:
            parsed = self._parse_response(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Claude intake response parse error sku=%s: %s | raw=%s",
                         request.sku, exc, raw[:300])
            raise ValueError(f"Claude intake returned unparseable JSON: {exc}") from exc

        return self._map_to_result(
            parsed, request,
            images_sent=images_sent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            selection=selection,
        )
