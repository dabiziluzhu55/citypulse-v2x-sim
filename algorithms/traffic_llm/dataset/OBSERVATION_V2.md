# Traffic Observation V2

Version string: `traffic_observation_v2`

V2 is a **compact SFT observation**. It is rebuilt from existing raw snapshots and does not overwrite Pilot V1 `sft/` JSONL or raw runs.

## Why V2 exists

Pilot V1 prompt tokens were too long for single-4090 QLoRA:

- P50 = 3892
- P95 = 7041
- max = 7088

Formal training uses TRL prompt-completion with `completion_only_loss=true`. If the prompt eats the `max_length` budget, assistant JSON is truncated and the model never sees the control label. V2 compresses the **input**, not the assistant plan.

Target: **P95 total tokens ≤ 3500**, assistant truncation rate = 0.

## Schema

```json
{
  "observation_version": "traffic_observation_v2",
  "scene": {"period": "morning_peak", "scope": "east_dense", "t": 120.0, "seed": 42001},
  "event": {"type": "lane_closure", "status": "active", "tgt": "demo_15", "t0": 120, "t1": 180, "lane": "-57228_0"},
  "controlled_region": ["demo_14", "demo_15", "demo_16"],
  "ix": {
    "demo_15": {
      "ph": 2,
      "lanes": [
        {"id": "-57228_0", "veh": 8, "halt": 5, "speed": 2.3, "occ": 0.61, "queue_m": 31.5, "wait": 12.0}
      ]
    }
  },
  "net": {"veh": 120, "halt": 40, "speed": 5.2},
  "allowed_phases": {"demo_15": [1, 2, 3]},
  "phase_service": {
    "demo_15": {
      "1": ["-57228_0:through", "-57228_1:through"],
      "2": ["-57217_0:left"],
      "3": ["-56907_0:through"]
    }
  }
}
```

### Lane fields kept

These change over a 30s signal plan and affect phase choice:

| V2 key | Source snapshot field |
|---|---|
| `id` | lane id |
| `veh` | `vehicle_count` |
| `halt` | `halting_count` |
| `speed` | `mean_speed` |
| `occ` | `occupancy` |
| `queue_m` | `queue_length_m` |
| `wait` | `waiting_time` |
| `ph` | intersection `current_phase` |

Only **incoming** (and `both`) lanes are kept.

### Fields dropped from per-lane / per-intersection JSON

| Dropped | Reason |
|---|---|
| `lane_length_m` | static geometry |
| `role` | V2 already filters to incoming |
| `pending_phase` | yellow/clearance transient; 5s slot plan uses executed target phase |
| `stage` | TLS internal stage, not a legal phase id |
| `prediction` / `prediction_available` | unused / empty in current dataset |
| outgoing lanes | do not decide the current green set |

## Phase semantics

`phase_service` is built from SUMO `tls_manifest.json` only:

1. For each intersection, read official `phase_order`.
2. For each phase, read `templates[phase][tls_id].green`.
3. For each connection, if `green[link_index]` is `g` or `G`, the connection is served.
4. Token is `incoming_lane:movement` from real `from_edge`/`from_lane`/`movement` (`through`/`left`/`right`). If movement is missing, `from_lane>to_lane`.

No phase-to-lane mapping is invented from phase numbers.

## Compatibility

- Raw traces and snapshots stay V1 compact fields.
- Pilot V1 SFT remains in `sft/`.
- Pilot V2 SFT is written to `sft_v2/` + `prompt_completion_v2/`.
- Formal V1 uses V2 as `sft/` + `prompt_completion/`.
