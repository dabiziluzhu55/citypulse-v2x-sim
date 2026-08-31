# [Archived] Dynamic lane graph v1

This stage adds one new model only: a sparse, sample-conditioned dynamic graph
for the existing official20 lane206 vehicle-count task. The existing
persistence, moving-average, historical-average, XGBoost, and static STGCN
artefacts are reference results and are not retrained.

## Frozen contract

- Nodes: the existing 206 incoming lanes, in the metadata order.
- Features: `vehicle_count`, `halting_count`, `mean_speed`, `occupancy`.
- Input: `[batch, 4, 12, 206]`.
- Target: the 12th prepared target frame, 60 seconds ahead.
- Snapshot interval: 5 seconds.
- Splits: 18 train episodes, 3 validation episodes, 3 ID test episodes, and
  6 OOD test episodes.
- Normalization: the parameters fitted by the existing training split.

## Commands

Build the sparse candidate graph from the unchanged static graph:

```powershell
python -m algorithms.prediction.build_dynamic_lane_graph `
  --adjacency <static-official20-lane-graph.npz> `
  --output <dynamic_lane_v1>/graph/dynamic_candidate_edges.npz `
  --report <dynamic_lane_v1>/graph/dynamic_candidate_edges.json
```

Run the read-only contract check and create the baseline reference:

```powershell
python -m algorithms.prediction.preflight_official20_lane206 `
  --dataset-dir <lane206-tensors> `
  --episode-file <one-lane206-episode.csv> `
  --graph <static-official20-lane-graph.npz> `
  --output-dir <dynamic_lane_v1> `
  --baseline-formal-dir <existing-lane206-formal-dir>
```

Run the disposable one-epoch CPU smoke test in an independent output folder:

```powershell
python -m algorithms.prediction.archive.experiments.train_dynamic_lane_v1 `
  --dataset-dir <lane206-tensors> `
  --graph <dynamic_lane_v1>/graph/dynamic_candidate_edges.npz `
  --output-dir <dynamic_lane_v1-smoke> `
  --baseline-reference <dynamic_lane_v1>/baseline_reference.json `
  --dry-run
```

The formal run uses the same entry point without `--dry-run`; use `--cpu` when
GPU is unavailable. `--resume` continues from `checkpoints/last.pt`, and
`--evaluate-only` recomputes the three lane-level test reports from
`checkpoints/best.pt`.

For the first diagnostic control experiment, fix every gate to `1` and use a
separate output directory:

```powershell
python -m algorithms.prediction.archive.experiments.train_dynamic_lane_v1 `
  --dataset-dir <lane206-tensors> `
  --graph <dynamic_lane_v1>/graph/dynamic_candidate_edges.npz `
  --output-dir <dynamic_lane_v1_static_sparse_control> `
  --baseline-reference <dynamic_lane_v1>/baseline_reference.json `
  --gate-mode fixed_one
```

This does not retrain or replace the existing static STGCN. It isolates the
new local sparse spatial layer from the learned dynamic gate.

## Spatial-layer alignment control

Because the sparse local spatial layer is not the same operator as the
existing STGCN's Chebyshev layer, the next control experiment uses a local
implementation that mirrors the existing reference model's fixed spatial
path: symmetric normalized Laplacian GSO, Chebyshev order `Ks=3`, two ST blocks,
and the same temporal/output channel layout. It still uses an independent
directory and reads the same sparse candidate archive to reconstruct the
unchanged static adjacency:

```powershell
python -m algorithms.prediction.archive.experiments.train_dynamic_lane_v1 `
  --dataset-dir <lane206-tensors> `
  --graph <dynamic_lane_v1>/graph/dynamic_candidate_edges.npz `
  --output-dir <dynamic_lane_v1_static_cheb_control> `
  --baseline-reference <dynamic_lane_v1>/baseline_reference.json `
  --spatial-mode static_cheb
```

`static_cheb` does not use a dynamic gate. Its purpose is to verify that the
local implementation and the reference STGCN spatial layer agree before the
sparse dynamic message-passing layer is changed again. The existing external
STGCN repository and all frozen baseline artefacts remain unchanged.

## Dynamic Chebyshev v2

After the spatial control is aligned, `dynamic_cheb` keeps the same temporal
and Chebyshev blocks but conditions the sparse adjacency on each sample:

```powershell
python -m algorithms.prediction.archive.experiments.train_dynamic_lane_v1 `
  --dataset-dir <lane206-tensors> `
  --graph <dynamic_lane_v1>/graph/dynamic_candidate_edges.npz `
  --output-dir <dynamic_lane_v1_dynamic_cheb_v2> `
  --baseline-reference <dynamic_lane_v1>/baseline_reference.json `
  --spatial-mode dynamic_cheb
```

The gate is generated from the latest frame and the 12-frame mean. One gate
is shared by the two directions of an undirected candidate pair, self-loops
stay at `1`, and the resulting sparse adjacency is symmetrically degree
normalized before the fixed-scale Chebyshev recurrence. The final gate layer
is zero-initialized, so training starts at the static Chebyshev control rather
than at an unrelated spatial operator.

Gate ablations can be run without changing the graph or data contract. For
example, the following adds a penalty `mean((gate - 1)^2)` while retaining the
original `[0.5, 1.5]` range:

```powershell
python -m algorithms.prediction.archive.experiments.train_dynamic_lane_v1 `
  --dataset-dir <lane206-tensors> `
  --graph <dynamic_lane_v1>/graph/dynamic_candidate_edges.npz `
  --output-dir <dynamic_lane_v1_dynamic_cheb_v2_reg> `
  --baseline-reference <dynamic_lane_v1>/baseline_reference.json `
  --spatial-mode dynamic_cheb `
  --gate-regularization 0.05
```

`--gate-half-range 0.2` is the corresponding narrower-boundary ablation and
maps the dynamic gate to `[0.8, 1.2]`.

## Dynamic edge rule

Each sample produces one gate per sparse candidate edge. The gate MLP sees the
source and target lane's 12-frame mean, latest frame, and the edge relation
encoding. It uses only the four frozen features. The gate is constrained to
`[0.5, 1.5]`, multiplies the static edge weight, and the incoming edge weights
of each target lane are normalized again. Self-loops remain weight `1` and are
not dynamically gated. The same edge-weight set is reused by both spatial
blocks.

## Output

The experiment directory contains the sparse graph, `best.pt` and `last.pt`,
lane-level validation/ID/OOD metrics, a copied baseline reference, a package
manifest, and `SHA256SUMS.txt`. `metrics/results_60s.csv` keeps the existing
baseline rows and appends `DynamicLaneGraphV1` rows with deltas against the
static STGCN. No route-level metrics, hierarchy, signal/queue fields, API,
frontend, Docker, control, or incident-detection changes belong to this stage.
