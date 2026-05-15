"""Deep item analysis request/response contract.

Provider-agnostic. The contract here is what an OpenAI/Gemini/Claude/local
provider would consume and produce. ``DeterministicDeepAnalysisProvider`` is a
conservative fallback that NEVER invents fields and instead surfaces
uncertainty for human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Protocol

from packages.core.src.constants import EXTRACTION_SCHEMA_VERSION
from packages.domain.src.entities.item import Item
from packages.intake.src.category_resolver import CategoryCandidate
from packages.intake.src.identity_scan import IdentityScanResult
from packages.intake.src.marketplace_requirements import MarketplaceRequirements
from packages.intake.src.photo_types import PhotoMeta, parse_photo_inputs
from packages.intake.src.pipeline_types import RiskFlag


@dataclass
class DeepAnalysisRequest:
    sku: str | None
    canonical_schema_version: str
    item: Item
    photo_meta: list[PhotoMeta] = field(default_factory=list)
    user_context: str | None = None
    identity: IdentityScanResult | None = None
    selected_category: CategoryCandidate | None = None
    candidate_categories: list[CategoryCandidate] = field(default_factory=list)
    marketplace_requirements: MarketplaceRequirements | None = None
    required_aspects: list[str] = field(default_factory=list)
    recommended_aspects: list[str] = field(default_factory=list)
    allowed_condition_ids: list[str] = field(default_factory=list)
    current_publish_blockers: list[str] = field(default_factory=list)
    current_intake_quality_status: str | None = None
    do_not_guess_policy: bool = True
    desired_json_schema: dict | None = None


@dataclass
class DeepAnalysisResult:
    sku: str | None
    suggested_field_updates: dict[str, Any] = field(default_factory=dict)
    confidence_by_field: dict[str, float] = field(default_factory=dict)
    evidence_by_field: dict[str, list[str]] = field(default_factory=dict)
    uncertain_fields: list[str] = field(default_factory=list)
    do_not_guess_fields: list[str] = field(default_factory=list)
    suggested_condition_id: str | None = None
    condition_assessment: str | None = None
    item_specifics: dict[str, Any] = field(default_factory=dict)
    title_suggestions: list[str] = field(default_factory=list)
    description_suggestion: str | None = None
    pricing_estimate: float | None = None
    pricing_evidence: list[str] = field(default_factory=list)
    authenticity_flags: list[str] = field(default_factory=list)
    high_value_flags: list[str] = field(default_factory=list)
    needs_more_photos: bool = False
    missing_photo_types: list[str] = field(default_factory=list)
    publish_risk_flags: list[str] = field(default_factory=list)
    correction_summary: list[str] = field(default_factory=list)
    should_require_manual_review: bool = True
    should_block_publish_approval: bool = True
    provider: str = "deterministic-fallback"

    def to_dict(self) -> dict:
        return asdict(self)


class DeepAnalysisProvider(Protocol):
    name: str

    def analyze(self, request: DeepAnalysisRequest) -> DeepAnalysisResult: ...


class DeterministicDeepAnalysisProvider:
    """Conservative deterministic deep analysis fallback.

    NEVER invents new field values. Suggested updates are limited to fields the
    item already has and which can be normalized safely. Flags uncertainty
    rather than fabricating data.
    """

    name = "deterministic-fallback"

    def analyze(self, request: DeepAnalysisRequest) -> DeepAnalysisResult:
        from packages.intake.src.quality_gate import evaluate_intake_quality

        item = request.item
        quality = evaluate_intake_quality(item)

        # Suggested updates: only echo existing values; never invent.
        suggestions: dict[str, Any] = {}
        confidence: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        uncertain: list[str] = []
        do_not_guess: list[str] = []

        for field_name in ("brand", "model", "material", "color", "size", "era",
                           "type", "subcategory", "department"):
            value = getattr(item, field_name, None)
            if value:
                suggestions[field_name] = value
                confidence[field_name] = 0.4
                evidence[field_name] = [f"existing item.{field_name} value"]
            else:
                uncertain.append(field_name)

        # Sensitive fields the deterministic provider must never guess.
        do_not_guess_set = {"authenticity", "first_edition", "signed_by"}
        do_not_guess.extend(sorted(do_not_guess_set))

        publish_risk_flags: list[str] = []
        if quality.needs_more_photos_for_analysis:
            publish_risk_flags.append(RiskFlag.MISSING_REQUIRED_PHOTOS)
        if request.current_publish_blockers:
            publish_risk_flags.extend(request.current_publish_blockers)
        if request.marketplace_requirements and request.marketplace_requirements.requires_live_read_only_fetch:
            publish_risk_flags.append(RiskFlag.MARKETPLACE_POLICY_UNKNOWN)

        authenticity_flags: list[str] = []
        high_value_flags: list[str] = []
        if (item.estimated_price or 0) >= 75:
            high_value_flags.append(RiskFlag.HIGH_VALUE_ESTIMATE)
        text_blob = " ".join(
            str(value or "").lower()
            for value in [item.title_final, item.title_raw, item.notes,
                          item.brand, item.condition_label]
        )
        if any(token in text_blob for token in ["coach", "gucci", "prada", "louis vuitton"]):
            authenticity_flags.append(RiskFlag.AUTHENTICITY_SENSITIVE_BRAND)

        correction_summary: list[str] = []
        if uncertain:
            correction_summary.append(
                "Uncertain fields require human confirmation: " + ", ".join(uncertain[:5])
            )
        if quality.missing_photo_types:
            correction_summary.append(
                "Add: " + ", ".join(quality.missing_photo_types[:5])
            )

        suggested_condition_id = item.condition_id if item.condition_id else None
        if (
            suggested_condition_id
            and request.allowed_condition_ids
            and suggested_condition_id not in request.allowed_condition_ids
        ):
            publish_risk_flags.append(RiskFlag.MALFORMED_CONDITION_ID)

        should_block = (
            quality.needs_more_photos_for_analysis
            or bool(authenticity_flags)
            or bool(high_value_flags)
            or bool(request.current_publish_blockers)
        )

        return DeepAnalysisResult(
            sku=request.sku or item.sku,
            suggested_field_updates=suggestions,
            confidence_by_field=confidence,
            evidence_by_field=evidence,
            uncertain_fields=uncertain,
            do_not_guess_fields=do_not_guess,
            suggested_condition_id=suggested_condition_id,
            condition_assessment=item.condition_label,
            item_specifics=dict(item.item_specifics or {}),
            title_suggestions=[item.title_final] if item.title_final else [],
            description_suggestion=item.description_final,
            pricing_estimate=item.estimated_price,
            pricing_evidence=[],
            authenticity_flags=authenticity_flags,
            high_value_flags=high_value_flags,
            needs_more_photos=quality.needs_more_photos_for_analysis,
            missing_photo_types=list(quality.missing_photo_types),
            publish_risk_flags=publish_risk_flags,
            correction_summary=correction_summary,
            should_require_manual_review=True,
            should_block_publish_approval=should_block,
            provider=self.name,
        )


def run_deep_analysis_preview(
    item: Item,
    identity: IdentityScanResult | None = None,
    selected_category: CategoryCandidate | None = None,
    marketplace_requirements: MarketplaceRequirements | None = None,
    user_context: str | None = None,
    provider: DeepAnalysisProvider | None = None,
) -> DeepAnalysisResult:
    provider = provider or DeterministicDeepAnalysisProvider()
    request = DeepAnalysisRequest(
        sku=item.sku,
        canonical_schema_version=EXTRACTION_SCHEMA_VERSION,
        item=item,
        photo_meta=parse_photo_inputs(item),
        user_context=user_context,
        identity=identity,
        selected_category=selected_category,
        marketplace_requirements=marketplace_requirements,
        required_aspects=list(marketplace_requirements.required_aspects) if marketplace_requirements else [],
        recommended_aspects=list(marketplace_requirements.recommended_aspects) if marketplace_requirements else [],
        allowed_condition_ids=list(marketplace_requirements.allowed_condition_ids) if marketplace_requirements else [],
        current_publish_blockers=[],
        current_intake_quality_status=item.intake_quality_status,
        do_not_guess_policy=True,
    )
    return provider.analyze(request)
