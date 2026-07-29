# Event detection multi-intersection validation (2026-07-29)

## Scope

This validation extends the earlier `demo_2` smoke to two topology/signal-different intersections: `demo_1` and `demo_3`. All runs use real SUMO/TraCI snapshots, a fixed seed (`42`), morning peak, and isolated server artifacts.

## Result

| Intersection | Scenario | Result |
| --- | --- | --- |
| demo_2 | normal | 2,440 detection rows; 0 alarms; no cards |
| demo_1 | normal | 2,150 detection rows; 0 alarms; no cards |
| demo_3 | normal | 1,168 detection rows; 0 alarms; no cards |
| demo_1 | lane closure | 37 `lane_blocked` alarms; card at `-56384_0` from 180 s |
| demo_3 | accident | 24 `accident` alarms; card at `-52565_1` from 245 s |
| demo_3 | spillback | 10 `spillback` alarms; card at `-50816_1` from 315 s |

Together with the earlier `demo_2` cases, normal, lane closure, accident, and spillback have reproducible successful cases across three intersections.

## Closure injection compatibility

Hard lane disallowance invalidated some global SUMO routes and terminated the run. For multi-intersection validation, a closure is represented as `lane_closure` with `max_speed: 0.1`: it preserves existing routes while explicitly reducing lane capacity to near zero.

The detector now treats green, occupied lanes with an explicit near-zero `current_allowed_speed_mps` as direct `lane_blocked` evidence. This path is intentionally independent of the generic CUSUM threshold: reducing that global threshold produced false positives on `demo_1` normal.

## Artifacts

All generated inputs and outputs are isolated under:

```text
/home/kemove/devdata1/zyh_v2x_ai/runs/event_detection_multisite_20260729/
```

Key files are `normal_demo{1,2,3}_detections_*.csv`, `demo1_lane_closure_detections_v6.csv`, `demo3_accident_detections_v1.csv`, `demo3_spillback_detections_v1.csv`, and their matching `*_cards_*.json` files.

## Checks

```bash
python -m unittest tests.test_event_detection_state tests.test_event_detection_cards \
  tests.test_disturbance_events tests.test_session_cli tests.test_event_detection_evaluate
```

Result: 34 tests passed.

## Remaining boundary

This is rule-baseline validation, not a recall benchmark. It uses one seed per scenario and does not yet prove every abnormal type on every intersection. Real-time backend wiring remains a separate integration task.
