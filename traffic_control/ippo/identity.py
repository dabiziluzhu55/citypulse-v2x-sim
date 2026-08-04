"""Canonical identity slots for IPPO v8 (20-intersection generalist model).

Slot order is an explicit checkpoint contract, never derived from string
sorting.  Any future network that changes the slot set must define a new
module-level constant and a new checkpoint contract version.
"""

from __future__ import annotations

IDENTITY_SLOT_IDS: tuple[str, ...] = (
    "demo_1", "demo_2", "demo_3", "demo_4", "demo_5",
    "demo_6", "demo_7", "demo_8", "demo_9", "demo_10",
    "demo_11", "demo_12", "demo_13", "demo_14", "demo_15",
    "demo_16", "demo_17", "demo_18", "demo_19", "demo_20",
)

IDENTITY_SLOT_INDEX: dict[str, int] = {
    intersection_id: index
    for index, intersection_id in enumerate(IDENTITY_SLOT_IDS)
}


def identity_slots_for(intersection_ids: object) -> tuple[int, ...]:
    """Return canonical slot indices for a controlled-intersection sequence.

    The 20-slot identity is an explicit checkpoint contract for IPPO v8.
    Intersections outside the canonical set are rejected instead of being
    assigned a string-sorted index; a future v9 network that changes the
    slot set must define its own slot table and contract version.
    """
    indices: list[int] = []
    for raw in intersection_ids:
        intersection_id = str(raw)
        slot = IDENTITY_SLOT_INDEX.get(intersection_id)
        if slot is None:
            raise ValueError(
                f"Intersection {intersection_id!r} is not in the canonical "
                f"IPPO identity slots: {IDENTITY_SLOT_IDS}"
            )
        indices.append(slot)
    return tuple(indices)
