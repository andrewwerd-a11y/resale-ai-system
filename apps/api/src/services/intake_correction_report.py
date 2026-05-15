from __future__ import annotations

from apps.api.src.services.publish_compatibility import evaluate_publish_compatibility
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from packages.domain.src.entities.item import Item
from packages.intake.src.quality_gate import evaluate_intake_quality


def build_intake_correction_report(item: Item) -> dict:
    quality = evaluate_intake_quality(item)
    readiness = evaluate_publish_readiness(item).as_dict()
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)
    next_actions = _next_action_sequence(
        quality=quality.as_dict(),
        readiness=readiness,
        compatibility=compatibility,
    )
    return {
        "sku": item.sku,
        "intake_quality": quality.as_dict(),
        "needs_more_photos_for_analysis": quality.needs_more_photos_for_analysis,
        "missing_photo_checklist": quality.missing_photo_types,
        "publish_readiness": {
            "ready": readiness.get("ready"),
            "blockers": readiness.get("blockers") or [],
            "required_actions": readiness.get("required_actions") or [],
        },
        "publish_compatibility": {
            "ready": compatibility.get("ready"),
            "blockers": compatibility.get("blockers") or [],
            "required_actions": compatibility.get("required_actions") or [],
        },
        "next_action_sequence": next_actions,
        "should_run_deep_analysis": quality.should_run_deep_analysis,
        "should_block_publish_approval": quality.should_block_publish_approval,
        "no_ebay_mutation_performed": True,
    }


def _next_action_sequence(*, quality: dict, readiness: dict, compatibility: dict) -> list[dict]:
    actions: list[dict] = []
    if quality.get("missing_photo_types"):
        actions.append(
            {
                "group": "Add more photos before analysis",
                "action": "Upload: " + ", ".join(quality.get("missing_photo_types") or []),
            }
        )
    if quality.get("intake_quality_status") == "NEEDS_AUTHENTICITY_REVIEW":
        actions.append(
            {
                "group": "Manual authenticity/high-value review",
                "action": "Complete manual review before deep analysis or approval.",
            }
        )
    if quality.get("intake_quality_status") == "NEEDS_CONDITION_REVIEW":
        actions.append(
            {
                "group": "Add condition context",
                "action": "Add condition notes, defects, or condition data before deep analysis.",
            }
        )
    for action in readiness.get("required_actions") or []:
        actions.append({"group": "Publish readiness", "action": str(action)})
    for action in compatibility.get("required_actions") or []:
        actions.append({"group": "Publish compatibility", "action": str(action)})
    if not actions:
        actions.append(
            {
                "group": "Ready for deep analysis",
                "action": "Run deep analysis, then require human approval before publish.",
            }
        )
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in actions:
        key = (entry["group"], entry["action"])
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped
