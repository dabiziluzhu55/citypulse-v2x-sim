#!/usr/bin/env bash
set -euo pipefail

# One-epoch TLS100 smoke test. It consumes aggregated junction CSVs and never
# applies the lane-level active-node filter.
PROJECT_DIR=${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}
SNAPSHOT_DIR=${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}
EXPERIMENT_DIR=${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}
STGCN_DIR=${STGCN_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/STGCN}
PYTHON=${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}
GPU_ID=${GPU_ID:-0}

BASE_DIR="$EXPERIMENT_DIR/tls100_intersection_v1"
INPUT_DIR="$BASE_DIR/raw"
SMOKE_DIR="$BASE_DIR/smoke_multifeature_v1"
MANIFEST_JSON="$BASE_DIR/episode_manifest.json"
ADJACENCY="$BASE_DIR/adjacency.npz"
NET="$SNAPSHOT_DIR/data/maps/sumo/generated/network/TotalMap_20.signals.net.xml"

export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_DIR"

[[ ! -e "$SMOKE_DIR" ]] || { echo "smoke directory already exists: $SMOKE_DIR" >&2; exit 2; }
[[ -s "$MANIFEST_JSON" && -s "$ADJACENCY" ]] || { echo "TLS100 aggregation artefacts are missing" >&2; exit 2; }
[[ -d "$STGCN_DIR" ]] || { echo "STGCN is missing" >&2; exit 2; }
command -v nvidia-smi >/dev/null || { echo "nvidia-smi is unavailable" >&2; exit 2; }
nvidia-smi -i "$GPU_ID" >/dev/null

mkdir -p "$SMOKE_DIR"
"$PYTHON" - "$MANIFEST_JSON" "$SMOKE_DIR/manifest.json" <<'PY'
import json
import sys
from collections import defaultdict
from pathlib import Path

source, output = map(Path, sys.argv[1:3])
episodes = json.loads(source.read_text(encoding="utf-8"))["episodes"]
wanted = ("train", "validation", "test_in_distribution", "test_extrapolation")
selected = []
for split in wanted:
    selected.append(next(item for item in episodes if item["split"] == split))
output.write_text(json.dumps({"episodes": selected}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

cd "$PROJECT_DIR"
"$PYTHON" -m algorithms.prediction.prepare_stgcn_episode_dataset --manifest "$SMOKE_DIR/manifest.json" --input-dir "$INPUT_DIR" --output-dir "$SMOKE_DIR/tensors" --net "$NET" --adjacency "$ADJACENCY" --target vehicle_count --feature vehicle_count --feature halting_count --feature mean_speed --feature occupancy --n-his 12 --n-pred 12
"$PYTHON" -m algorithms.prediction.evaluate_stage1_baselines --dataset-dir "$SMOKE_DIR/tensors" --output "$SMOKE_DIR/baselines.csv"
"$PYTHON" -m algorithms.prediction.train_xgboost_stage1 --dataset-dir "$SMOKE_DIR/tensors" --output-dir "$SMOKE_DIR/xgb" --max-train-rows 2000 --n-estimators 2 --max-depth 3 --n-jobs 2 --seed 42
CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 --dataset-dir "$SMOKE_DIR/tensors" --stgcn-root "$STGCN_DIR" --output-dir "$SMOKE_DIR/stgcn" --epochs 1 --patience 1 --batch-size 32 --seed 42 --horizon-step 12
[[ -s "$SMOKE_DIR/xgb/metrics.json" && -s "$SMOKE_DIR/stgcn/metrics.json" ]] || { echo "smoke metrics are incomplete" >&2; exit 2; }
echo "smoke_completed=$SMOKE_DIR"
