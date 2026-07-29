"""Deterministic, read-only project context overview."""
from __future__ import annotations

from typing import Any, Mapping

from .context_search import ITEM_TYPES, _eligible, _project, normalize


HIGHLIGHT_LIMITS = {"decision": 3, "risk": 5, "open_question": 5, "action": 5}


def overview(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return only safe projections; never mutate items or call a model."""
    current = [item for item in items if item.get("kind") == "context" and item.get("current") is True]
    confirmed = [item for item in current if _eligible(item)]
    workflow = {
        "confirmed": len(confirmed),
        "requires_review": sum(item.get("status") == "requires_review" for item in current),
        "conflicted": sum(item.get("status") == "conflicted" for item in current),
    }
    workflow["pending_total"] = workflow["requires_review"] + workflow["conflicted"]
    counts = {item_type: sum(item.get("item_type") == item_type for item in confirmed) for item_type in ITEM_TYPES}
    actions = [item for item in confirmed if item.get("item_type") == "action"]
    attention = {
        "actions_without_actor": sum(not str(item.get("actor_ref") or "").strip() for item in actions),
        "actions_without_deadline": sum(not str(item.get("deadline") or "").strip() for item in actions),
        "related_items": sum(bool(item.get("relation_ids")) for item in confirmed),
    }
    highlights = {
        "decisions": _highlights(confirmed, "decision", HIGHLIGHT_LIMITS["decision"]),
        "risks": _highlights(confirmed, "risk", HIGHLIGHT_LIMITS["risk"]),
        "open_questions": _highlights(confirmed, "open_question", HIGHLIGHT_LIMITS["open_question"]),
        "actions": _highlights(confirmed, "action", HIGHLIGHT_LIMITS["action"]),
    }
    actors: dict[str, tuple[str, int]] = {}
    for item in confirmed:
        value = str(item.get("actor_ref") or "").strip()
        key = normalize(value)
        if key:
            previous = actors.get(key, (value, 0))
            actors[key] = (previous[0], previous[1] + 1)
    return {
        "current_context_count": len(current),
        "workflow": workflow,
        "counts": counts,
        "attention": attention,
        "highlights": highlights,
        "actors": [{"value": value, "count": count} for _, (value, count) in sorted(actors.items())],
    }


def _highlights(items: list[Mapping[str, Any]], item_type: str, limit: int) -> list[dict[str, Any]]:
    selected = [item for item in items if item.get("item_type") == item_type]
    if item_type == "action":
        selected.sort(key=lambda item: (not bool(str(item.get("deadline") or "").strip()), _descending(item), normalize(item.get("title"))))
    else:
        selected.sort(key=lambda item: (_descending(item), normalize(item.get("title"))))
    return [_project(item) for item in selected[:limit]]


def _descending(item: Mapping[str, Any]) -> str:
    # Invert only the sort call by using a second stable reverse sort is needlessly
    # opaque here; ISO timestamps sort naturally after this marker in reverse order.
    return "".join(chr(0x10FFFF - ord(char)) for char in str(item.get("updated_at") or ""))
