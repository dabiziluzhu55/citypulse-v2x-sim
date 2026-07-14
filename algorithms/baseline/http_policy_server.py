"""Minimal remote policy service for end-to-end interface testing.

Run with:
    uvicorn algorithms.baseline.http_policy_server:app --host 127.0.0.1 --port 8001
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException


app = FastAPI(title="CityPulse example control policy")
_metadata: dict | None = None


@app.post("/reset")
def reset(metadata: dict) -> dict:
    global _metadata
    if not isinstance(metadata.get("intersections"), dict):
        raise HTTPException(status_code=400, detail="intersections metadata is required")
    _metadata = metadata
    return {"ready": True}


@app.post("/act")
def act(observation: dict) -> dict:
    if _metadata is None:
        raise HTTPException(status_code=409, detail="call /reset before /act")
    phases = {}
    for intersection_id, state in observation.get("intersections", {}).items():
        metadata = _metadata["intersections"].get(intersection_id)
        if metadata is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown intersection {intersection_id}",
            )
        approach_queues = {
            approach: sum(int(lane["halting_count"]) for lane in lanes)
            for approach, lanes in state["approaches"].items()
        }
        scores = {
            int(phase): sum(approach_queues[name] for name in movement[1])
            for phase, movement in metadata["phase_movements"].items()
        }
        best_score = max(scores.values())
        tied = {phase for phase, score in scores.items() if score == best_score}
        order = [int(value) for value in metadata["phase_order"]]
        current_index = order.index(int(state["current_phase"]))
        rotated = order[current_index + 1 :] + order[: current_index + 1]
        phases[intersection_id] = next(phase for phase in rotated if phase in tied)
    return {"signal_phases": phases, "vehicle_advisories": {}}


@app.post("/close")
def close() -> dict:
    global _metadata
    _metadata = None
    return {"closed": True}
