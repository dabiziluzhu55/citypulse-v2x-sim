"""Strong-MP parity controller that records normalized q for SCREEN only."""
from __future__ import annotations

from typing import Any, Mapping

from algorithms.cov2x.road.mp_prior import (
    StrongMPPressureOracle,
    normalized_phase_prior,
)
from traffic_control.protocol import (
    finish_response,
    initialize_response,
    signals_from_phase_map,
    step_response,
)


_episode_id = ""
_minimum_green = 5.0
_phase_orders: dict[str, tuple[int, ...]] = {}
_oracle: StrongMPPressureOracle | None = None
_current_scores: list[dict[int, float]] = []
_completed_scores: list[list[dict[int, float]]] = []


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _episode_id, _minimum_green, _phase_orders, _oracle, _current_scores
    _episode_id = str(payload.get("episode_id", ""))
    _minimum_green = float(payload.get("minimum_green", 5.0) or 5.0)
    _phase_orders = {
        str(tls_id): tuple(int(value) for value in (item.get("phase_order") or ()))
        for tls_id, item in (payload.get("intersections", {}) or {}).items()
    }
    _oracle = StrongMPPressureOracle(payload)
    _current_scores = []
    return initialize_response(episode_id=_episode_id)


def _legal_phases(item: Mapping[str, Any], order: tuple[int, ...]) -> tuple[int, ...]:
    current = int(item.get("current_phase", order[0]))
    pending = item.get("pending_phase")
    stage = str(item.get("stage", "GREEN")).upper()
    stage_elapsed = float(item.get("stage_elapsed", 0.0) or 0.0)
    if stage not in {"G", "GREEN"} or pending is not None or stage_elapsed + 1e-9 < _minimum_green:
        return (int(pending) if pending is not None else current,)
    return tuple(int(value) for value in (item.get("legal_phases") or order))


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    if _oracle is None:
        raise RuntimeError("SCREEN controller is not initialized")
    actions: dict[str, int] = {}
    intersections = payload.get("intersections", {}) or {}
    for tls_id, order in _phase_orders.items():
        if not order:
            continue
        item = intersections.get(tls_id, {}) or {}
        current = int(item.get("current_phase", order[0]))
        legal = _legal_phases(item, order)
        q_scores = normalized_phase_prior(
            payload,
            tls_id,
            order,
            oracle=_oracle,
            legal_phases=legal,
        )
        if len(q_scores) >= 2:
            _current_scores.append(dict(q_scores))
        if not q_scores:
            actions[tls_id] = current
            continue
        best_value = max(q_scores.values())
        ties = [
            phase
            for phase in order
            if phase in q_scores and abs(q_scores[phase] - best_value) <= 1e-9
        ]
        actions[tls_id] = current if current in ties else ties[0]
    return step_response(
        episode_id=str(payload["episode_id"]),
        step_id=payload["step_id"],
        signals=signals_from_phase_map(actions),
    )


def finish(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _oracle, _current_scores
    already = _oracle is None
    if not already:
        _completed_scores.append(list(_current_scores))
    _oracle = None
    _current_scores = []
    return finish_response(already_finished=already)


def take_score_sets() -> list[dict[int, float]] | None:
    return _completed_scores.pop(0) if _completed_scores else None
