# Event detection smoke handoff (2026-07-29)

## Scope

This delivery is the `demo_2` SUMO rule baseline for structured traffic-state event detection. It does not use CARLA images or video. The detector consumes per-lane SUMO/TraCI snapshots and emits detection rows plus frontend/backend-ready event cards.

Supported event demonstrations:

- normal: no alert;
- lane closure: `lane_blocked` card;
- downstream queue: `spillback` card;
- stopped/crashed vehicle: `accident` card.

## Verified result

The isolated server smoke run used real SUMO injection and exported snapshots. The matching normal controls produced zero alerts.

| Scenario | Result |
| --- | --- |
| normal | 0 alerts |
| lane closure | `lane_blocked` detected and card emitted |
| queue spillback | 11 detection rows and 1 `spillback` card |
| accident | 10 detection rows and an `accident` card |
| normal accident control | 0 alerts |

The accident injection stops an existing SUMO vehicle; it does not create a synthetic vehicle dynamically.

## Key code and outputs

- Rules and batch CLI: `algorithms/event_detection/rules.py`
- Event-card generation: `algorithms/event_detection/cards.py`
- Event semantics: `algorithms/event_detection/semantics.py`
- SUMO events and snapshot fields: `simulation/sumo/events.py`, `simulation/sumo/session.py`, `simulation/sumo/export_snapshots.py`
- Usage and data contract: `algorithms/event_detection/README.md`

Server artifacts are isolated under:

```text
/home/kemove/devdata1/zyh_v2x_ai/runs/event_detection_smoke_20260729_loader_compat/
```

The relevant outputs include `*_lanes.csv`, `*_detections.csv`, and `*_cards.json` for normal, spillback, and accident controls/cases.

## Backend handoff

The backend already exposes simulation creation, event injection, metrics, and a WebSocket snapshot stream. It does not yet run or expose this detector.

Backend integration should:

1. Feed every simulation snapshot into `EventDetectionObserver` during a live run.
2. Persist or retain the latest detection cards and summary.
3. Include `event_detection` cards in the WebSocket payload and add a read endpoint, for example `GET /simulations/{id}/event-detection`.
4. Add a `queue_spillback` event request if the frontend needs to inject that scenario (the current request schema supports lane closure, speed limit, and accident only).
5. Set `EVENT_DETECTION_ENABLE_ACCIDENT=true` when accident detection is required; it is deliberately opt-in in the observer configuration.

## Current boundary and next work

This is a usable single-intersection `demo_2` baseline, not a generalized production detector. It has been validated with one topology, fixed event parameters, and matching normal controls. The next event-detection milestone is to repeat the same four scenario checks on two additional topology/signal-different intersections, then complete backend real-time wiring.

`current_allowed_speed_mps` is exported for the capacity-restriction path, but in the current smoke it reported `0.0`; the successful accident demonstration relied on the traffic-state evidence path. This should be investigated before claiming capacity-based accident detection.

## Local verification

The committed test set passed:

```bash
python -m unittest tests.test_event_detection_state tests.test_event_detection_cards \
  tests.test_disturbance_events tests.test_session_cli tests.test_event_detection_evaluate
```

Result: 33 tests passed.
