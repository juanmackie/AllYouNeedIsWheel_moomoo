"""Diff two published decision sets (pure, no I/O).

The dashboard answers one question first: **what changed since I last looked?**
That only works when each action has a stable identity and a content revision
(see ``core.action_plan``). This module turns two ``next_actions`` lists into the
minimum a user must read:

* ``new``      — subjects with no decision in the previous publication
* ``changed``  — same subject, different revision, with the material field names
* ``resolved`` — subjects that had a decision and no longer do
* ``unchanged_count`` — subjects whose decision is identical

Reordering alone produces nothing: rank is not part of identity or revision.
"""

from __future__ import annotations

MATERIAL_FIELD_LABELS = {
    "verdict": "action changed",
    "urgency": "urgency changed",
    "wheel_state": "holding state changed",
    "deadline": "deadline changed",
    "ticket_kind": "ticket type changed",
    "legs": "ticket changed (strike, size, price or order)",
    "loss_if_called": "assignment loss changed",
    "if_called_pnl_vs_basis": "assignment result changed",
    "reason_codes": "blocking reason changed",
}


def _action_key(action) -> str:
    if not isinstance(action, dict):
        return ""
    return str(action.get("subject_id") or action.get("action_id") or "")


def _summary(action) -> dict:
    return {
        "action_id": action.get("action_id") or _action_key(action),
        "underlying": action.get("underlying"),
        "wheel_state": action.get("wheel_state"),
        "verdict": action.get("verdict"),
        "deadline": action.get("deadline"),
        "rank": action.get("rank"),
    }


def changed_fields(previous: dict, current: dict) -> list[str]:
    """Material field names that differ between two revisions of one decision."""
    before = previous.get("material") or {}
    after = current.get("material") or {}
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


def diff_actions(previous_actions=(), current_actions=()) -> dict:
    """Compare two published ``next_actions`` lists by subject identity."""
    previous = {_action_key(action): action for action in previous_actions or () if isinstance(action, dict)}
    current = {_action_key(action): action for action in current_actions or () if isinstance(action, dict)}

    # A previous publication without revisions cannot be compared: its content
    # was never hashed, so claiming "changed" or "unchanged" would be invented.
    if previous and any(not action.get("revision") for action in previous.values()):
        return {
            "comparable": False,
            "reason": "the previous publication predates change tracking",
            "new": [],
            "changed": [],
            "resolved": [],
            "unchanged_count": 0,
            "material_change_count": 0,
            "previous_action_count": len(previous),
            "current_action_count": len(current),
        }

    new = [_summary(action) for key, action in current.items() if key not in previous]
    resolved = [_summary(previous[key]) for key in previous if key not in current]
    changed = []
    unchanged = 0
    for key, action in current.items():
        before = previous.get(key)
        if before is None:
            continue
        if before.get("revision") == action.get("revision"):
            unchanged += 1
            continue
        fields = changed_fields(before, action)
        changed.append(
            {
                **_summary(action),
                "fields": fields,
                "labels": [MATERIAL_FIELD_LABELS.get(field, field) for field in fields],
                "previous_verdict": before.get("verdict"),
                "previous_revision": before.get("revision"),
            }
        )
    return {
        "comparable": True,
        "new": new,
        "changed": changed,
        "resolved": resolved,
        "unchanged_count": unchanged,
        "material_change_count": len(new) + len(changed) + len(resolved),
        "previous_action_count": len(previous),
        "current_action_count": len(current),
    }
