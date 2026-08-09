#!/usr/bin/env bash
set -euo pipefail

# Formal TLS100 junction-level prediction trainer. GPU is required only here.
PROJECT_DIR=${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}
SNAPSHOT_DIR=${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}
EXPERIMENT_DIR=${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}
STGCN_DIR=${STGCN_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/STGCN}
PYTHON=${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}
GPU_ID=${GPU_ID:-0}
EPOCHS=${EPOCHS:-100}
PATIENCE=${PATIENCE:-15}
ALLOW_ACTUAL_SPLITS=${ALLOW_ACTUAL_SPLITS:-0}

BASE_DIR="$EXPERIMENT_DIR/tls100_intersection_v1"
INPUT_DIR="$BASE_DIR/raw"
FORMAL_DIR="$BASE_DIR/formal"
TENSORS_DIR="$FORMAL_DIR/tensors"
MANIFEST_JSON="$BASE_DIR/episode_manifest.json"
ADJACENCY="$BASE_DIR/adjacency.npz"
NET="$SNAPSHOT_DIR/data/maps/sumo/generated/network/TotalMap_20.signals.net.xml"

export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_DIR"

require_file() {
  [[ -s "$1" ]] || { echo "missing or empty: $1" >&2; exit 2; }
}

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

require_file "$MANIFEST_JSON"
require_file "$ADJACENCY"
require_file "$INPUT_DIR"
require_file "$STGCN_DIR"
# The precomputed TLS100 adjacency makes the SUMO net unnecessary during
# training; keep NET as an optional provenance/prepare argument.

"$PYTHON" - "$MANIFEST_JSON" "$INPUT_DIR" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
raw_dir = Path(sys.argv[2])
if manifest.get("episode_count") != 30 or len(manifest.get("episodes", [])) != 30:
    raise SystemExit("TLS100 episode manifest must contain exactly 30 episodes")
for item in manifest["episodes"]:
    output = raw_dir / item["file"]
    metadata = Path(str(output) + ".metadata.json")
    if not output.is_file() or not metadata.is_file():
        raise SystemExit(f"missing aggregate pair: {output}")
    summary = json.loads(metadata.read_text(encoding="utf-8"))
    if summary.get("node_count") != 100 or summary.get("snapshot_count") != 721:
        raise SystemExit(f"invalid aggregate metadata: {metadata}")
PY

"$PYTHON" - "$MANIFEST_JSON" "$ALLOW_ACTUAL_SPLITS" <<'PY'
import json
import sys
from collections import Counter

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
actual = dict(sorted(Counter(item["split"] for item in manifest["episodes"]).items()))
expected = {
    "train": 18,
    "validation": 6,
    "test_in_distribution": 3,
    "test_extrapolation": 3,
}
if actual != expected and sys.argv[2] != "1":
    raise SystemExit(
        "split counts differ from the handoff expectation: "
        f"actual={actual}, expected={expected}. "
        "Do not silently change the split; after explicit confirmation set "
        "ALLOW_ACTUAL_SPLITS=1."
    )
print(f"split_counts={actual}")
if actual != expected:
    print("accepted_actual_split_counts=1")
PY

command -v nvidia-smi >/dev/null || { echo "nvidia-smi is unavailable" >&2; exit 2; }
nvidia-smi -i "$GPU_ID" >/dev/null

cd "$PROJECT_DIR"
mkdir -p "$FORMAL_DIR"
if [[ ! -s "$TENSORS_DIR/metadata.json" ]]; then
  if [[ -d "$TENSORS_DIR" && -n "$(find "$TENSORS_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "partial tensor artefacts exist without metadata; refusing to overwrite: $TENSORS_DIR" >&2
    exit 2
  fi
  "$PYTHON" -m algorithms.prediction.prepare_stgcn_episode_dataset --manifest "$MANIFEST_JSON" --input-dir "$INPUT_DIR" --output-dir "$TENSORS_DIR" --net "$NET" --adjacency "$ADJACENCY" --target vehicle_count --feature vehicle_count --feature halting_count --feature mean_speed --feature occupancy --n-his 12 --n-pred 12
fi

BASELINE_CSV="$FORMAL_DIR/baseline_metrics_three.csv"
BASELINE_JSON="$FORMAL_DIR/baseline_metrics_three.json"
if ! baseline_metrics_have_smape "$BASELINE_JSON"; then
  [[ ! -e "$BASELINE_CSV" && ! -e "$BASELINE_JSON" ]] || { echo "partial baseline artefacts exist; refusing to overwrite" >&2; exit 2; }
  "$PYTHON" -m algorithms.prediction.evaluate_stage1_baselines --dataset-dir "$TENSORS_DIR" --output "$BASELINE_CSV"
fi

XGB_DIR="$FORMAL_DIR/xgb"
if ! metrics_have_smape "$XGB_DIR/metrics.json"; then
  if [[ -s "$XGB_DIR/model.json" ]]; then
    "$PYTHON" -m algorithms.prediction.train_xgboost_stage1 --dataset-dir "$TENSORS_DIR" --output-dir "$XGB_DIR" --evaluate-only
  elif [[ -e "$XGB_DIR" && -n "$(find "$XGB_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "partial XGBoost artefacts exist without model.json; refusing to overwrite: $XGB_DIR" >&2
    exit 2
  else
    mkdir -p "$XGB_DIR"
    "$PYTHON" -m algorithms.prediction.train_xgboost_stage1 --dataset-dir "$TENSORS_DIR" --output-dir "$XGB_DIR" --max-train-rows 250000 --n-estimators 300 --n-jobs 8 --seed 42 > "$FORMAL_DIR/xgb.log" 2>&1
  fi
fi

STGCN_OUTPUT="$FORMAL_DIR/stgcn"
if ! metrics_have_smape "$STGCN_OUTPUT/metrics.json"; then
  if [[ -s "$STGCN_OUTPUT/best.pt" ]]; then
    CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 --dataset-dir "$TENSORS_DIR" --stgcn-root "$STGCN_DIR" --output-dir "$STGCN_OUTPUT" --batch-size 32 --horizon-step 12 --evaluate-only
  else
    STGCN_ARGS=()
    if [[ -s "$STGCN_OUTPUT/last.pt" ]]; then
      STGCN_ARGS+=(--resume)
    elif [[ -d "$STGCN_OUTPUT" && -n "$(find "$STGCN_OUTPUT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      echo "partial STGCN artefacts exist without last.pt; refusing to overwrite: $STGCN_OUTPUT" >&2
      exit 2
    fi
    mkdir -p "$STGCN_OUTPUT"
    CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 --dataset-dir "$TENSORS_DIR" --stgcn-root "$STGCN_DIR" --output-dir "$STGCN_OUTPUT" --epochs "$EPOCHS" --patience "$PATIENCE" --batch-size 32 --seed 42 --horizon-step 12 "${STGCN_ARGS[@]}"
  fi
fi

  "$PYTHON" -m algorithms.prediction.archive.aggregation.build_tls100_results_table --formal-dir "$FORMAL_DIR"
