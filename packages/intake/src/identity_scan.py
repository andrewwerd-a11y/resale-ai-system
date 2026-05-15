"""First-pass identity scan contract.

Provider-agnostic. Defines the request/result schema and a deterministic
fallback that uses only the existing item fields and filename heuristics — no
external API calls. Real OpenAI/Gemini/Claude/local providers can implement the
``IntakeIdentityProvider`` protocol later.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol

from packages.domain.src.entities.item import Item
from packages.intake.src.photo_types import PhotoMeta, parse_photo_inputs
from packages.intake.src.pipeline_types import IntakeDecision, RiskFlag


@dataclass
class IdentityScanRequest:
    sku: str | None
    item: Item
    photo_meta: list[PhotoMeta] = field(default_factory=list)
    user_context: str | None = None


@dataclass
class IdentityScanResult:
    sku: str | None
    object_type_guess: str | None = None
    brand_guess: str | None = None
    model_guess: str | None = None
    character_guess: str | None = None
    material_guess: str | None = None
    era_guess: str | None = None
    category_family_candidates: list[str] = field(default_factory=list)
    confidence: float = 0.0
    visual_evidence: list[str] = field(default_factory=list)
    uncertain_fields: list[str] = field(default_factory=list)
    needs_more_photos: bool = False
    missing_photo_types: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    should_continue_to_category_resolution: bool = False
    decision: str = IntakeDecision.LOW_CONFIDENCE_HOLD
    reason: str = ""
    provider: str = "deterministic-fallback"

    def to_dict(self) -> dict:
        return asdict(self)


class IntakeIdentityProvider(Protocol):
    name: str

    def analyze(self, request: IdentityScanRequest) -> IdentityScanResult: ...


class DeterministicIdentityProvider:
    """Conservative fallback that never calls an external API.

    Uses only existing item fields, title text, and filename hints. Designed to
    say "I'm not confident" loudly when evidence is weak.
    """

    name = "deterministic-fallback"

    def analyze(self, request: IdentityScanRequest) -> IdentityScanResult:
        from packages.intake.src.quality_gate import (
            category_family_for_item,
            evaluate_intake_quality,
        )

        item = request.item
        photo_meta = request.photo_meta or parse_photo_inputs(item)
        quality = evaluate_intake_quality(item)
        family = category_family_for_item(item)

        visual_evidence: list[str] = []
        if family != "unknown":
            visual_evidence.append(f"category_family inferred as {family}")
        for meta in photo_meta:
            if meta.photo_type != "unknown":
                visual_evidence.append(f"{meta.photo_type} photo present ({meta.path})")

        uncertain_fields: list[str] = []
        for attr in ("brand", "model", "material", "era"):
            if not getattr(item, attr, None):
                uncertain_fields.append(attr)

        risk_flags: list[str] = []
        if family == "unknown":
            risk_flags.append(RiskFlag.CATEGORY_UNCERTAIN)
        if quality.confidence < 0.55:
            risk_flags.append(RiskFlag.LOW_CONFIDENCE)
        if quality.needs_more_photos_for_analysis:
            risk_flags.append(RiskFlag.MISSING_REQUIRED_PHOTOS)
        if (item.estimated_price or 0) >= 75:
            risk_flags.append(RiskFlag.HIGH_VALUE_ESTIMATE)

        if quality.needs_more_photos_for_analysis:
            decision = IntakeDecision.NEEDS_MORE_PHOTOS
            reason = "Insufficient required photo coverage for first-pass identity."
            should_continue = False
        elif family == "unknown":
            decision = IntakeDecision.NEEDS_CATEGORY_REVIEW
            reason = "Category family could not be determined from existing fields."
            should_continue = False
        elif quality.confidence < 0.55:
            decision = IntakeDecision.LOW_CONFIDENCE_HOLD
            reason = "Existing confidence is below threshold for first-pass identity."
            should_continue = False
        else:
            decision = IntakeDecision.READY_FOR_DEEP_ANALYSIS
            reason = "Deterministic fallback found enough context to continue."
            should_continue = True

        return IdentityScanResult(
            sku=request.sku or item.sku,
            object_type_guess=item.type or item.category_label,
            brand_guess=item.brand,
            model_guess=item.model,
            character_guess=item.character,
            material_guess=item.material,
            era_guess=item.era,
            category_family_candidates=[family] if family else [],
            confidence=quality.confidence,
            visual_evidence=visual_evidence,
            uncertain_fields=uncertain_fields,
            needs_more_photos=quality.needs_more_photos_for_analysis,
            missing_photo_types=list(quality.missing_photo_types),
            risk_flags=risk_flags,
            should_continue_to_category_resolution=should_continue,
            decision=decision,
            reason=reason,
            provider=self.name,
        )


def run_first_pass_identity(
    item: Item,
    user_context: str | None = None,
    provider: IntakeIdentityProvider | None = None,
) -> IdentityScanResult:
    provider = provider or DeterministicIdentityProvider()
    request = IdentityScanRequest(
        sku=item.sku,
        item=item,
        photo_meta=parse_photo_inputs(item),
        user_context=user_context,
    )
    return provider.analyze(request)
