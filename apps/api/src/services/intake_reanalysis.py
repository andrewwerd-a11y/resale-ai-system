"""Reanalysis preview service.

Given a set of pending user edits (no DB write yet), produce a preview of:
- which downstream stages those edits would invalidate
- whether deep analysis should rerun
- whether publish-readiness dry-run should rerun
- per-field trust assessment / warnings

Read-only. Never persists.
"""
from __future__ import annotations

from typing import Any

from packages.domain.src.entities.item import Item
from packages.intake.src.correction_pipeline import (
    ChangeEvent,
    classify_change_event_impacts,
    classify_manual_edit,
)


def build_reanalysis_preview(
    item: Item,
    pending_updates: dict[str, Any],
) -> dict:
    events: list[ChangeEvent] = []
    trust_assessments: list[dict] = []
    blocked: list[dict] = []
    for field_name, new_value in (pending_updates or {}).items():
        old_value = getattr(item, field_name, None)
        if old_value == new_value:
            continue
        assessment = classify_manual_edit(field_name, old_value, new_value)
        trust_assessments.append(assessment.to_dict())
        if assessment.blocks_save:
            blocked.append(assessment.to_dict())
        events.append(
            ChangeEvent(
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                source="user",
                trust_level=assessment.trust_level,
            )
        )

    impacts = classify_change_event_impacts(events)
    return {
        "sku": item.sku,
        "pending_change_events": [e.to_dict() for e in events],
        "trust_assessments": trust_assessments,
        "blocked_edits": blocked,
        "impact_summary": impacts,
        "no_ebay_mutation_performed": True,
        "no_external_provider_called": True,
    }
