"""Frozen signal-side policies for Stage 2 vehicle learning.

The vehicle policy is trained against a deterministic, frozen signal
controller.  ``max_pressure`` selects the phase with the largest sum of
approach queue length, tie-breaking toward the currently running phase to
avoid gratuitous switching.  ``fixed`` holds the current phase.

This module is intentionally torch-free so it can be unit-tested on machines
without a PyTorch runtime.
"""

from __future__ import annotations

from typing import Any, Mapping


def _phase_pressures(
    payload: Mapping[str, Any],
    phase_orders: Mapping[str, tuple[int, ...]],
    lane_state_module: Any,
) -> dict[str, dict[int, float]]:
    """Per-phase pressure (sum of approach queue length) for each TLS."""
    intersections = payload.get("intersections", {}) or {}
    result: dict[str, dict[int, float]] = {}
    for tls_id, order in phase_orders.items():
        obs = intersections.get(tls_id) or {}
        lanes = obs.get("lanes", {}) or {}
        movements_by_phase = lane_state_module.phase_movements(tls_id)
        pressure = {int(phase_id): 0.0 for phase_id in order}
        for lane_id, lane_obs in lanes.items():
            if not isinstance(lane_obs, Mapping):
                continue
            lane_movements = lane_state_module.lane_movements(lane_id)
            if not lane_movements:
                continue
            try:
                queue_m = float(lane_obs.get("queue_length_m", 0.0) or 0.0)
            except (TypeError, ValueError):
                queue_m = 0.0
            for phase_id, allowed in movements_by_phase.items():
                if any(movement in allowed for movement in lane_movements):
                    pressure[int(phase_id)] += queue_m
        result[str(tls_id)] = pressure
    return result


def max_pressure_actions(
    payload: Mapping[str, Any],
    phase_orders: Mapping[str, tuple[int, ...]],
    lane_state_module: Any,
) -> dict[str, dict[str, int]]:
    """Return ``{tls_id: {"target_phase": phase}}`` under MaxPressure."""
    intersections = payload.get("intersections", {}) or {}
    pressures = _phase_pressures(payload, phase_orders, lane_state_module)
    actions: dict[str, dict[str, int]] = {}
    for tls_id, order in phase_orders.items():
        obs = intersections.get(tls_id) or {}
        current = int(obs.get("current_phase", order[0]) or order[0])
        candidates = pressures.get(str(tls_id), {})
        if not candidates:
            actions[str(tls_id)] = {"target_phase": current}
            continue
        best_phase = max(
            candidates,
            key=lambda phase_id: (
                candidates[phase_id],
                int(phase_id) == current,
            ),
        )
        actions[str(tls_id)] = {"target_phase": int(best_phase)}
    return actions


def fixed_actions(
    payload: Mapping[str, Any],
    phase_orders: Mapping[str, tuple[int, ...]],
) -> dict[str, dict[str, int]]:
    """Hold the currently running phase (or the first legal phase)."""
    intersections = payload.get("intersections", {}) or {}
    actions: dict[str, dict[str, int]] = {}
    for tls_id, order in phase_orders.items():
        obs = intersections.get(tls_id) or {}
        current = int(obs.get("current_phase", order[0]) or order[0])
        if current not in order:
            current = int(order[0])
        actions[str(tls_id)] = {"target_phase": current}
    return actions
