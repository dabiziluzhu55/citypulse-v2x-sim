# Official-20 prediction model handoff

## Scope

This handoff is for model packaging and inference integration only. It does
not authorize or include FastAPI, frontend, or deployment changes.

The deployed model is `official20-stgcn-v1`: an STGCN-Cheb direct forecast of
`vehicle_count` 60 seconds ahead for the official 20 intersections.

The corresponding source revision is `f0adbf3` (`feat(prediction): add
official20 forecasting pipeline`).

## Inference contract

| Field | Value |
| --- | --- |
| Nodes | 20 official intersections, in the `metadata.json` node order |
| Features | `vehicle_count`, `halting_count`, `mean_speed`, `occupancy` |
| Input cadence | 5 seconds |
| Input window | 12 snapshots (60 seconds) |
| Input shape | `[batch, 4, 12, 20]` after train-only normalization |
| Output | `[batch, 20]` predicted `vehicle_count` at +60 seconds |
| Fallback | Per-node 12-snapshot `moving_average` |

The node order for the current artefact is:

```text
demo_1, demo_10, demo_11, demo_12, demo_13, demo_14, demo_15, demo_16,
demo_17, demo_18, demo_19, demo_2, demo_20, demo_3, demo_4, demo_5,
demo_6, demo_7, demo_8, demo_9
```

The normalization statistics are fitted on the training split only. The
authoritative values, feature order, node order, and adjacency are supplied
by the artefact metadata; consumers must not infer them from display order.

## Artefact package

Publish the following as the `official20-prediction-v1` GitHub Release asset
(or an equivalent team-approved artifact store), rather than committing model
binaries to the source branch:

```text
official20-prediction-v1/
  stgcn_best.pt
  xgboost_model.json
  model_manifest.json
  adjacency.npz
  normalization_and_nodes.json
  stgcn_metrics.json
  xgb_metrics.json
  results_summary_60s.csv
  sha256sums.txt
  README.md
```

`model_manifest.json` must record the source commit, STGCN architecture
parameters, feature order, input/output shapes, and package file checksums.
`normalization_and_nodes.json` is a sanitized extraction of the training
metadata: do not ship server paths, raw episode paths, or credentials.

## Current validated files

The following files are retained on the school server until the release asset
is created. Verify their SHA-256 before packaging.

| File | SHA-256 |
| --- | --- |
| `stgcn/best.pt` | `de8eea0345dac842106a74ca6ef0db898fb6fae145ec595a8411fcfeb2ef4179` |
| `xgb/model.json` | `526f228852dc2a535cbab6557d76c9ef889a5623346ea8ae91383dcacc9fa88c` |
| `tensors/adjacency.npz` | `f89b3de246cea8e5b028b50000fd2ebb57d33178adb7833d6df805e8845448f9` |
| `stgcn/metrics.json` | `2996f50e8e66e5bccb1b61342c764040d0288ff9a32eacd91f3aa312d2a8dfb4` |
| `xgb/metrics.json` | `ea04234967cae2d37f5b7eaf1ee42085fdae9fd0e0f70079423cc870215920c2` |

Runtime used for this validated run:

```text
Python: v2x-ai-py310
PyTorch: 2.13.0+cu130
XGBoost: 3.2.0
GPU during training: physical GPU 1 (RTX 4090)
```

The result tables committed under `docs/results/` have their own SHA-256
checksums recorded in the release package.
