#!/usr/bin/env bash
set -euo pipefail

# Reproducible lane-level formal trainer.  Nodes are exactly the official 20
# intersections' incoming lanes from the TLS manifest (currently 206 nodes),
# never the full SUMO network.  Raw snapshots are read only; derived artefacts
# are isolated in lane206_v1 and the STGCN is pinned to GPU 1 by default.

PROJECT_DIR="${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}"
EXPERIMENT_DIR="${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}"
STGCN_DIR="${STGCN_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/STGCN}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}"
PYTHON="${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}"
GPU_ID="${GPU_ID:-1}"
EPOCHS="${EPOCHS:-100}"
PATIENCE="${PATIENCE:-15}"

BASE_DIR="$EXPERIMENT_DIR/lane206_v1"
RAW_DIR="$EXPERIMENT_DIR/raw"
MANIFEST_TSV="$EXPERIMENT_DIR/collection_manifest_prediction_v1.tsv"
TLS_MANIFEST="$SNAPSHOT_DIR/data/maps/sumo/generated/manifests/tls_manifest.json"
NETWORK="$SNAPSHOT_DIR/data/maps/sumo/generated/network/TotalMap_20.signals.net.xml"
GRAPH_DIR="$BASE_DIR/graph"
ADJACENCY="$GRAPH_DIR/official20_lane_adjacency.npz"
FORMAL_DIR="$BASE_DIR/formal"

export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"

require_file() { [[ -s "$1" ]] || { echo "missing or empty: $1" >&2; exit 2; }; }
metrics_have_smape() {
  [[ -s "$1" ]] || return 1
  "$PYTHON" - "$1" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    required = ("validation", "test_in_distribution", "test_extrapolation")
    ok = all(isinstance(payload.get(split), dict) and "smape" in payload[split] for split in required)
except (OSError, ValueError, TypeError):
    ok = False
raise SystemExit(0 if ok else 1)
PY
}
baseline_metrics_have_smape() {
  [[ -s "$1" ]] || return 1
  "$PYTHON" - "$1" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    ok = isinstance(payload, list) and bool(payload) and all("smape" in row for row in payload)
except (OSError, ValueError, TypeError):
    ok = False
raise SystemExit(0 if ok else 1)
PY
}

require_file "$MANIFEST_TSV"
[[ -d "$RAW_DIR" ]] || { echo "missing raw directory: $RAW_DIR" >&2; exit 2; }
[[ -d "$STGCN_DIR" ]] || { echo "missing STGCN directory: $STGCN_DIR" >&2; exit 2; }
command -v nvidia-smi >/dev/null || { echo "nvidia-smi is unavailable" >&2; exit 2; }
nvidia-smi -i "$GPU_ID" >/dev/null

mkdir -p "$GRAPH_DIR" "$FORMAL_DIR"
if [[ ! -s "$ADJACENCY" ]]; then
  require_file "$TLS_MANIFEST"
  require_file "$NETWORK"
  "$PYTHON" -m algorithms.prediction.build_official20_lane_adjacency \
    --tls-manifest "$TLS_MANIFEST" --net "$NETWORK" --output "$ADJACENCY" \
    --report-dir "$GRAPH_DIR/topology" --max-hops 4
fi

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
if {row["split"] for row in episodes} != {"train", "validation", "test_in_distribution", "test_extrapolation"}:
    raise SystemExit("unexpected prediction split set")
payload = {"episodes": [
    {
        "id": row["id"], "split": row["split"],
        "demand_scale": float(row["demand_scale"]), "seed": int(row["seed"]),
        "file": f"{row['id']}_5s_lanes.csv",
    }
    for row in episodes
]}
output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
fi

mapfile -t INPUT_ARGS < <("$PYTHON" - "$MANIFEST_JSON" "$RAW_DIR" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["episodes"]:
    print("--input")
    print(f"{sys.argv[2]}/{item['file']}")
PY
)

cd "$PROJECT_DIR"
if [[ ! -s "$FORMAL_DIR/filtered/official20_lane_filter_metadata.json" ]]; then
  "$PYTHON" -m algorithms.prediction.filter_official20_lane_snapshots \
    --adjacency "$ADJACENCY" "${INPUT_ARGS[@]}" --output-dir "$FORMAL_DIR/filtered"
fi
if [[ ! -s "$FORMAL_DIR/tensors/metadata.json" ]]; then
  "$PYTHON" -m algorithms.prediction.prepare_stgcn_episode_dataset \
    --manifest "$MANIFEST_JSON" --input-dir "$FORMAL_DIR/filtered" \
    --output-dir "$FORMAL_DIR/tensors" --net /dev/null --adjacency "$ADJACENCY" \
    --target vehicle_count \
    --feature vehicle_count --feature halting_count --feature mean_speed --feature occupancy \
    --n-his 12 --n-pred 12
fi
if ! baseline_metrics_have_smape "$FORMAL_DIR/baseline_metrics_three.json"; then
  "$PYTHON" -m algorithms.prediction.evaluate_stage1_baselines \
    --dataset-dir "$FORMAL_DIR/tensors" --output "$FORMAL_DIR/baseline_metrics_three.csv"
fi

xgb_pid=""
if ! metrics_have_smape "$FORMAL_DIR/xgb/metrics.json"; then
  if [[ -s "$FORMAL_DIR/xgb/model.json" ]]; then
    "$PYTHON" -m algorithms.prediction.train_xgboost_stage1 \
      --dataset-dir "$FORMAL_DIR/tensors" --output-dir "$FORMAL_DIR/xgb" --evaluate-only
  elif [[ -e "$FORMAL_DIR/xgb.log" ]]; then
    echo "partial XGBoost artefacts found; refuse to overwrite them" >&2
    exit 2
  else
    "$PYTHON" -m algorithms.prediction.train_xgboost_stage1 \
      --dataset-dir "$FORMAL_DIR/tensors" --output-dir "$FORMAL_DIR/xgb" \
      --max-train-rows 250000 --n-estimators 300 --n-jobs 8 \
      > "$FORMAL_DIR/xgb.log" 2>&1 &
    xgb_pid=$!
  fi
fi

if ! metrics_have_smape "$FORMAL_DIR/stgcn/metrics.json"; then
  if [[ -s "$FORMAL_DIR/stgcn/best.pt" ]]; then
    CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 \
      --dataset-dir "$FORMAL_DIR/tensors" --stgcn-root "$STGCN_DIR" \
      --output-dir "$FORMAL_DIR/stgcn" --batch-size 32 --horizon-step 12 --evaluate-only
  else
    stgcn_args=()
    if [[ -e "$FORMAL_DIR/stgcn/last.pt" ]]; then
      stgcn_args+=(--resume)
    elif [[ -d "$FORMAL_DIR/stgcn" ]] && [[ -n "$(find "$FORMAL_DIR/stgcn" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      echo "partial STGCN artefacts found without last.pt; refuse to overwrite them" >&2
      exit 2
    fi
    CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 \
      --dataset-dir "$FORMAL_DIR/tensors" --stgcn-root "$STGCN_DIR" \
      --output-dir "$FORMAL_DIR/stgcn" --epochs "$EPOCHS" --patience "$PATIENCE" \
      --batch-size 32 --seed 42 --horizon-step 12 "${stgcn_args[@]}"
  fi
fi
if [[ -n "$xgb_pid" ]]; then
  wait "$xgb_pid"
fi

"$PYTHON" -m algorithms.prediction.build_official20_results_table --formal-dir "$FORMAL_DIR"
