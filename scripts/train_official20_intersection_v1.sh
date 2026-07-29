#!/usr/bin/env bash
set -euo pipefail

# Reproducible, non-destructive formal trainer for the official 20-intersection
# task. It reuses completed artefacts and resumes STGCN only from last.pt.

PROJECT_DIR="${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}"
EXPERIMENT_DIR="${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}"
STGCN_DIR="${STGCN_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/STGCN}"
PYTHON="${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}"
GPU_ID="${GPU_ID:-1}"
EPOCHS="${EPOCHS:-100}"
PATIENCE="${PATIENCE:-15}"

BASE_DIR="$EXPERIMENT_DIR/intersection20_v1"
RAW_DIR="$BASE_DIR/raw"
ADJACENCY="$BASE_DIR/adjacency.npz"
FORMAL_DIR="$BASE_DIR/formal"
MANIFEST_TSV="$EXPERIMENT_DIR/collection_manifest_prediction_v1.tsv"

export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"

require_file() { [[ -s "$1" ]] || { echo "missing or empty: $1" >&2; exit 2; }; }
require_file "$MANIFEST_TSV"
require_file "$ADJACENCY"
[[ -d "$RAW_DIR" ]] || { echo "missing raw directory: $RAW_DIR" >&2; exit 2; }
[[ -d "$STGCN_DIR" ]] || { echo "missing STGCN directory: $STGCN_DIR" >&2; exit 2; }
command -v nvidia-smi >/dev/null || { echo "nvidia-smi is unavailable" >&2; exit 2; }
nvidia-smi -i "$GPU_ID" >/dev/null

mkdir -p "$FORMAL_DIR"
MANIFEST_JSON="$FORMAL_DIR/manifest.json"
if [[ ! -s "$MANIFEST_JSON" ]]; then
  "$PYTHON" - "$MANIFEST_TSV" "$MANIFEST_JSON" <<'PY'
import csv
import json
import sys
from pathlib import Path

source, output = map(Path, sys.argv[1:])
episodes = list(csv.DictReader(source.open(encoding="utf-8"), delimiter="\t"))
if len(episodes) != 30:
    raise SystemExit(f"expected 30 episodes, found {len(episodes)}")
payload = {"episodes": [
    {
        "id": row["id"], "split": row["split"],
        "demand_scale": float(row["demand_scale"]), "seed": int(row["seed"]),
        "file": f"{row['id']}_5s_intersections.csv",
    }
    for row in episodes
]}
output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
fi

mapfile -t TRAIN_ARGS < <("$PYTHON" - "$MANIFEST_JSON" "$RAW_DIR" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["episodes"]:
    if item["split"] == "train":
        print("--train-input")
        print(f"{sys.argv[2]}/{item['file']}")
PY
)
mapfile -t INPUT_ARGS < <("$PYTHON" - "$MANIFEST_JSON" "$RAW_DIR" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["episodes"]:
    print("--input")
    print(f"{sys.argv[2]}/{item['file']}")
PY
)

cd "$PROJECT_DIR"
if [[ ! -s "$FORMAL_DIR/filtered/active_lane_metadata.json" ]]; then
  "$PYTHON" -m algorithms.prediction.filter_active_lanes \
    "${TRAIN_ARGS[@]}" "${INPUT_ARGS[@]}" \
    --output-dir "$FORMAL_DIR/filtered" --min-active-samples 1 --min-active-ratio 0
fi
if [[ ! -s "$FORMAL_DIR/tensors/metadata.json" ]]; then
  "$PYTHON" -m algorithms.prediction.prepare_stgcn_episode_dataset \
    --manifest "$MANIFEST_JSON" --input-dir "$FORMAL_DIR/filtered" \
    --output-dir "$FORMAL_DIR/tensors" --net /dev/null --adjacency "$ADJACENCY" \
    --target vehicle_count \
    --feature vehicle_count --feature halting_count --feature mean_speed --feature occupancy \
    --n-his 12 --n-pred 12
fi
if [[ ! -s "$FORMAL_DIR/baseline_metrics_three.json" ]]; then
  "$PYTHON" -m algorithms.prediction.evaluate_stage1_baselines \
    --dataset-dir "$FORMAL_DIR/tensors" --output "$FORMAL_DIR/baseline_metrics_three.csv"
fi

xgb_pid=""
if [[ ! -s "$FORMAL_DIR/xgb/metrics.json" ]]; then
  if [[ -e "$FORMAL_DIR/xgb/model.json" || -e "$FORMAL_DIR/xgb.log" ]]; then
    echo "partial XGBoost artefacts found; refuse to overwrite them" >&2
    exit 2
  fi
  "$PYTHON" -m algorithms.prediction.train_xgboost_stage1 \
    --dataset-dir "$FORMAL_DIR/tensors" --output-dir "$FORMAL_DIR/xgb" \
    --max-train-rows 250000 --n-estimators 300 --n-jobs 8 \
    > "$FORMAL_DIR/xgb.log" 2>&1 &
  xgb_pid=$!
fi

if [[ ! -s "$FORMAL_DIR/stgcn/metrics.json" ]]; then
  stgcn_args=()
  if [[ -e "$FORMAL_DIR/stgcn" ]]; then
    stgcn_args+=(--resume)
  fi
  CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 \
    --dataset-dir "$FORMAL_DIR/tensors" --stgcn-root "$STGCN_DIR" \
    --output-dir "$FORMAL_DIR/stgcn" --epochs "$EPOCHS" --patience "$PATIENCE" \
    --batch-size 32 --seed 42 --horizon-step 12 "${stgcn_args[@]}"
fi
if [[ -n "$xgb_pid" ]]; then
  wait "$xgb_pid"
fi

"$PYTHON" -m algorithms.prediction.build_official20_results_table --formal-dir "$FORMAL_DIR"
