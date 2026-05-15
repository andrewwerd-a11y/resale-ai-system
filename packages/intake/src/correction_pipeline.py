"""Canonical item proposal layer, manual-edit trust classifier, and
reanalysis impact classifier.

The system does NOT blindly apply AI output to item records. Anything that
would mutate the canonical item must first become a ``CanonicalItemProposal``
that records source, evidence, confidence, and the before/after diff so a
human (or a later approval workflow) can accept or reject the proposal.

Manual user edits are classified into trust levels so the UI can surface
warnings on risky claims (e.g., authenticity, brand) without permanently
blocking intentional overrides.

A change event (field, old, new) is also classified by what downstream
stages it could invalidate: identity, category, condition, pricing,
marketplace requirements, publish readiness. Callers use this to decide
whether to re-run deep analysis and/or readiness checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import DeepAnalysisResult
from packages.intake.src.pipeline_types import (
    ManualEditTrustLevel,
    RiskFlag,
)


# Fields the system will never auto-overwrite on the canonical item, even from
# a high-confidence proposal. These require explicit human approval.
NEVER_AUTO_OVERWRITE: frozenset[str] = frozenset({
    "brand",
    "authenticity",
    "condition_id",
    "condition_label",
    "ebay_category_id",
    "ebay_category_name",
    "estimated_price",
})

# Fields safe to auto-fill if the canonical item has no current value AND
# the proposal confidence meets a minimum threshold.
SAFE_AUTOFILL_WHEN_EMPTY: frozenset[str] = frozenset({
    "color",
    "material",
    "type",
    "subcategory",
    "department",
    "format",
    "language",
})

# Fields whose values, when edited by a user, are treated as factual
# observations (low risk, no authenticity weight).
FACTUAL_OBSERVATION_FIELDS: frozenset[str] = frozenset({
    "color",
    "type",
    "subcategory",
    "department",
    "size",
    "format",
    "language",
    "pattern",
    "style",
})

# Fields where a user edit creates a "risky claim" that should surface a
# warning (authenticity / high-value / category-impacting).
RISKY_CLAIM_FIELDS: frozenset[str] = frozenset({
    "brand",
    "model",
    "edition",
    "era",
    "isbn",
    "ebay_category_id",
    "condition_id",
    "condition_label",
})

# Measurement-bearing fields (any Measurements numeric field, plus
# weight/shipping fields).
MEASUREMENT_FIELDS: frozenset[str] = frozenset({
    "measurements",
    "shipping_weight",
})

AUTHENTICITY_KEYWORDS: frozenset[str] = frozenset({
    "authentic", "genuine", "real", "original", "certified", "verified",
})


@dataclass
class CanonicalFieldProposal:
    field_name: str
    old_value: Any = None
    new_value: Any = None
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    requires_human_approval: bool = True
    safe_to_autofill: bool = False
    never_auto_overwrite: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CanonicalItemProposal:
    sku: str | None
    source_stage: str
    source_provider: str
    field_proposals: list[CanonicalFieldProposal] = field(default_factory=list)
    overall_confidence: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    fields_requiring_human_approval: list[str] = field(default_factory=list)
    fields_safe_to_autofill: list[str] = field(default_factory=list)
    fields_never_auto_overwrite: list[str] = field(default_factory=list)
    before_after_diff: dict[str, dict[str, Any]] = field(default_factory=dict)
    pricing_impact: float | None = None
    category_impact: bool = False
    publish_readiness_impact: bool = False
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "source_stage": self.source_stage,
            "source_provider": self.source_provider,
            "field_proposals": [p.to_dict() for p in self.field_proposals],
            "overall_confidence": self.overall_confidence,
            "risk_flags": self.risk_flags,
            "fields_requiring_human_approval": self.fields_requiring_human_approval,
            "fields_safe_to_autofill": self.fields_safe_to_autofill,
            "fields_never_auto_overwrite": self.fields_never_auto_overwrite,
            "before_after_diff": self.before_after_diff,
            "pricing_impact": self.pricing_impact,
            "category_impact": self.category_impact,
            "publish_readiness_impact": self.publish_readiness_impact,
            "created_at": self.created_at,
        }


def build_canonical_proposal_from_deep_analysis(
    item: Item,
    deep_result: DeepAnalysisResult,
    *,
    source_stage: str,
) -> CanonicalItemProposal:
    """Convert a DeepAnalysisResult into a CanonicalItemProposal.

    Important: this never writes to the item record. Callers decide what (if
    anything) to apply, and only after human approval.
    """
    proposals: list[CanonicalFieldProposal] = []
    requires_approval: list[str] = []
    safe_autofill: list[str] = []
    never_overwrite: list[str] = []
    diff: dict[str, dict[str, Any]] = {}

    for field_name, new_value in (deep_result.suggested_field_updates or {}).items():
        old_value = getattr(item, field_name, None)
        if old_value == new_value:
            continue
        confidence = float(deep_result.confidence_by_field.get(field_name, 0.0))
        evidence = list(deep_result.evidence_by_field.get(field_name, []))
        proposal_risks: list[str] = []

        is_never = field_name in NEVER_AUTO_OVERWRITE
        is_safe = (
            field_name in SAFE_AUTOFILL_WHEN_EMPTY
            and not is_never
            and (old_value is None or old_value == "")
            and confidence >= 0.7
        )

        if is_never:
            proposal_risks.append(RiskFlag.NEEDS_MANUAL_REVIEW)
            never_overwrite.append(field_name)
            requires_approval.append(field_name)
        elif not is_safe:
            requires_approval.append(field_name)
        else:
            safe_autofill.append(field_name)

        proposals.append(
            CanonicalFieldProposal(
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                confidence=confidence,
                evidence=evidence,
                risk_flags=proposal_risks,
                requires_human_approval=is_never or not is_safe,
                safe_to_autofill=is_safe,
                never_auto_overwrite=is_never,
            )
        )
        diff[field_name] = {"old": old_value, "new": new_value}

    overall_confidence = (
        sum(p.confidence for p in proposals) / len(proposals) if proposals else 0.0
    )
    risk_flags = list(deep_result.publish_risk_flags or [])
    if deep_result.authenticity_flags:
        risk_flags.extend(deep_result.authenticity_flags)
    if deep_result.high_value_flags:
        risk_flags.extend(deep_result.high_value_flags)

    pricing_impact = None
    if deep_result.pricing_estimate is not None and item.estimated_price is not None:
        try:
            pricing_impact = float(deep_result.pricing_estimate) - float(item.estimated_price)
        except (TypeError, ValueError):
            pricing_impact = None
    elif deep_result.pricing_estimate is not None:
        pricing_impact = float(deep_result.pricing_estimate)

    category_impact = any(
        p.field_name in {"ebay_category_id", "ebay_category_name", "category_key"}
        for p in proposals
    )
    publish_readiness_impact = bool(deep_result.publish_risk_flags) or category_impact

    return CanonicalItemProposal(
        sku=item.sku,
        source_stage=source_stage,
        source_provider=deep_result.provider,
        field_proposals=proposals,
        overall_confidence=round(overall_confidence, 3),
        risk_flags=sorted(set(risk_flags)),
        fields_requiring_human_approval=sorted(set(requires_approval)),
        fields_safe_to_autofill=sorted(set(safe_autofill)),
        fields_never_auto_overwrite=sorted(set(never_overwrite)),
        before_after_diff=diff,
        pricing_impact=pricing_impact,
        category_impact=category_impact,
        publish_readiness_impact=publish_readiness_impact,
    )


@dataclass
class ManualEditTrustAssessment:
    field_name: str
    trust_level: str
    warnings: list[str] = field(default_factory=list)
    requires_evidence: bool = False
    blocks_save: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def classify_manual_edit(
    field_name: str,
    old_value: Any,
    new_value: Any,
) -> ManualEditTrustAssessment:
    """Classify a single user-submitted field edit.

    Pure function. Does not write to the item. The system can warn but should
    not permanently block an intentional manual override — ``blocks_save`` is
    always False here. (Routes may still gate writes elsewhere.)
    """
    warnings: list[str] = []
    requires_evidence = False
    new_text = str(new_value or "").lower()

    if field_name in MEASUREMENT_FIELDS:
        trust = ManualEditTrustLevel.FACTUAL_MEASUREMENT
    elif field_name in FACTUAL_OBSERVATION_FIELDS:
        trust = ManualEditTrustLevel.FACTUAL_OBSERVATION
    elif field_name in RISKY_CLAIM_FIELDS:
        trust = ManualEditTrustLevel.RISKY_CLAIM
        warnings.append(
            f"Field '{field_name}' is a risky claim; evidence (photo or note) is recommended."
        )
        requires_evidence = True
    elif any(token in new_text for token in AUTHENTICITY_KEYWORDS):
        trust = ManualEditTrustLevel.RISKY_CLAIM
        warnings.append(
            "Authenticity-sensitive language detected; provide supporting evidence."
        )
        requires_evidence = True
    elif old_value not in (None, "") and new_value != old_value:
        trust = ManualEditTrustLevel.OVERRIDE
        warnings.append(
            f"Overriding existing value '{old_value}'; operator decision recorded."
        )
    else:
        trust = ManualEditTrustLevel.USER_CLAIM

    return ManualEditTrustAssessment(
        field_name=field_name,
        trust_level=trust,
        warnings=warnings,
        requires_evidence=requires_evidence,
        blocks_save=False,
    )


@dataclass
class ChangeEvent:
    field_name: str
    old_value: Any
    new_value: Any
    source: str = "user"  # user | model | system
    trust_level: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    notes: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReanalysisImpact:
    field_name: str
    affects_identity: bool = False
    affects_category: bool = False
    affects_condition: bool = False
    affects_pricing: bool = False
    affects_marketplace_required_fields: bool = False
    affects_publish_readiness: bool = False
    should_rerun_deep_analysis: bool = False
    should_rerun_dry_run_readiness: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_FIELDS_AFFECTING_IDENTITY = {"brand", "model", "type", "character", "subcategory"}
_FIELDS_AFFECTING_CATEGORY = {
    "ebay_category_id",
    "ebay_category_name",
    "category_key",
    "category_label",
    "subcategory",
    "department",
}
_FIELDS_AFFECTING_CONDITION = {"condition_id", "condition_label", "condition_notes", "defects"}
_FIELDS_AFFECTING_PRICING = {"estimated_price", "list_price", "minimum_price", "cost", "size"}
_FIELDS_AFFECTING_MARKETPLACE_REQS = {
    "ebay_category_id",
    "brand",
    "size",
    "color",
    "material",
    "type",
    "department",
}


def classify_change_event_impact(event: ChangeEvent) -> ReanalysisImpact:
    name = event.field_name
    reasons: list[str] = []

    affects_identity = name in _FIELDS_AFFECTING_IDENTITY
    affects_category = name in _FIELDS_AFFECTING_CATEGORY
    affects_condition = name in _FIELDS_AFFECTING_CONDITION
    affects_pricing = name in _FIELDS_AFFECTING_PRICING
    affects_marketplace = name in _FIELDS_AFFECTING_MARKETPLACE_REQS
    measurement_change = name in MEASUREMENT_FIELDS

    if affects_identity:
        reasons.append("Edit changes identity-relevant field; deep analysis should rerun.")
    if affects_category:
        reasons.append("Edit changes category; marketplace requirements must rerun.")
    if affects_condition:
        reasons.append("Edit changes condition; publish readiness must rerun.")
    if affects_pricing:
        reasons.append("Edit changes pricing-relevant field.")
    if affects_marketplace:
        reasons.append("Edit may change marketplace-required fields.")
    if measurement_change:
        reasons.append("Measurement edit; deep analysis may improve.")

    should_rerun_deep = (
        affects_identity or affects_category or affects_condition or measurement_change
    )
    should_rerun_readiness = (
        affects_category or affects_condition or affects_marketplace
    )

    return ReanalysisImpact(
        field_name=name,
        affects_identity=affects_identity,
        affects_category=affects_category,
        affects_condition=affects_condition,
        affects_pricing=affects_pricing,
        affects_marketplace_required_fields=affects_marketplace,
        affects_publish_readiness=affects_category or affects_condition or affects_marketplace,
        should_rerun_deep_analysis=should_rerun_deep,
        should_rerun_dry_run_readiness=should_rerun_readiness,
        reasons=reasons,
    )


def classify_change_event_impacts(
    events: list[ChangeEvent],
) -> dict:
    """Aggregate impact across multiple change events."""
    impacts = [classify_change_event_impact(e) for e in events]
    return {
        "events": [imp.to_dict() for imp in impacts],
        "should_rerun_deep_analysis": any(imp.should_rerun_deep_analysis for imp in impacts),
        "should_rerun_dry_run_readiness": any(imp.should_rerun_dry_run_readiness for imp in impacts),
        "affects_identity": any(imp.affects_identity for imp in impacts),
        "affects_category": any(imp.affects_category for imp in impacts),
        "affects_condition": any(imp.affects_condition for imp in impacts),
        "affects_pricing": any(imp.affects_pricing for imp in impacts),
        "affects_marketplace_required_fields": any(
            imp.affects_marketplace_required_fields for imp in impacts
        ),
        "affects_publish_readiness": any(imp.affects_publish_readiness for imp in impacts),
    }
