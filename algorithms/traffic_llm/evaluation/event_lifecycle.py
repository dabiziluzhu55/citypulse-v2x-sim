"""Extract disturbance event lifecycle from SUMO snapshots or persisted runs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


ACTIVE = "ACTIVE"
COMPLETED = "COMPLETED"
SCHEDULED = "SCHEDULED"
FAILED = "FAILED"


def _event_items(snapshot: Any) -> list[Mapping[str, Any]]:
    if snapshot is None:
        return []
    if hasattr(snapshot, "events"):
        rows = []
        for item in snapshot.events or ():
            rows.append(
                {
                    "event_id": getattr(item, "event_id", None),
                    "event_type": getattr(item, "event_type", None),
                    "state": getattr(item, "state", None),
                    "error": getattr(item, "error", None),
                    "elapsed_seconds": getattr(snapshot, "elapsed_seconds", None),
                }
            )
        return rows
    if isinstance(snapshot, Mapping):
        summary = dict(snapshot.get("summary") or {})
        raw = summary.get("events") or snapshot.get("events") or ()
        elapsed = snapshot.get("elapsed_seconds") or summary.get("elapsed_seconds")
        rows = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            payload.setdefault("elapsed_seconds", elapsed)
            rows.append(payload)
        return rows
    return []


def collect_event_lifecycle(snapshots: Sequence[Any] | None) -> dict[str, Any]:
    """Return ordered unique states, e.g. SCHEDULED → ACTIVE → COMPLETED."""

    states: list[str] = []
    errors: list[str] = []
    timeline: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in snapshots or ():
        for item in _event_items(snapshot):
            state = str(item.get("state") or "")
            if not state:
                continue
            timeline.append(
                {
                    "elapsed_seconds": item.get("elapsed_seconds"),
                    "state": state,
                    "error": item.get("error"),
                    "event_type": item.get("event_type"),
                }
            )
            if state not in seen:
                seen.add(state)
                states.append(state)
            err = item.get("error")
            if err:
                text = str(err)
                if text not in errors:
                    errors.append(text)
    activated = ACTIVE in seen
    return {
        "states": states,
        "activated": activated,
        "completed": COMPLETED in seen,
        "failed": FAILED in seen,
        "valid_for_compare": activated,
        "errors": errors[:8],
        "timeline_head": timeline[:8],
        "timeline_tail": timeline[-4:] if len(timeline) > 8 else [],
    }


def lifecycle_from_run(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    existing = run.get("event_lifecycle")
    if isinstance(existing, Mapping) and existing.get("states"):
        return dict(existing)
    snapshots = run.get("snapshots") or run.get("compact_snapshots")
    if snapshots:
        return collect_event_lifecycle(list(snapshots))
    decisions = run.get("decisions") or ()
    states: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for decision in decisions:
        for item in decision.get("events") or ():
            state = str(item.get("state") or "")
            if state and state not in seen:
                seen.add(state)
                states.append(state)
            err = item.get("error")
            if err and str(err) not in errors:
                errors.append(str(err))
        if decision.get("event_id") and not decision.get("skipped"):
            if ACTIVE not in seen:
                seen.add(ACTIVE)
                states.append(ACTIVE)
    if states:
        return {
            "states": states,
            "activated": ACTIVE in seen,
            "completed": COMPLETED in seen,
            "failed": FAILED in seen,
            "valid_for_compare": ACTIVE in seen,
            "errors": errors[:8],
        }
    if int(run.get("n_plans") or 0) > 0:
        return {
            "states": [ACTIVE],
            "activated": True,
            "completed": None,
            "failed": False,
            "valid_for_compare": True,
            "errors": [],
            "inferred_from": "n_plans",
        }
    return None


def event_is_valid_for_compare(*runs: Mapping[str, Any] | None) -> bool:
    """A scenario may enter effect comparison only if every present run saw ACTIVE."""

    present = [item for item in runs if item]
    if not present:
        return False
    for run in present:
        life = lifecycle_from_run(run)
        if life is None:
            if int(run.get("n_plans") or 0) > 0:
                continue
            skipped = [
                item
                for item in (run.get("decisions") or ())
                if item.get("skipped") == "event_not_active"
            ]
            if skipped and int(run.get("n_plans") or 0) == 0:
                return False
            continue
        if not life.get("activated"):
            return False
    return True
