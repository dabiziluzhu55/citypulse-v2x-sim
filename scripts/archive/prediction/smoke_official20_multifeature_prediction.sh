#!/usr/bin/env bash
set -euo pipefail

# Minimal preflight for the four-feature STGCN + XGBoost pipeline.  It uses
# one episode per split, tiny XGBoost and one STGCN epoch; formal directories
# and results are never touched.
PROJECT_DIR="${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}"
EXPERIMENT_DIR="${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}"
STGCN_DIR="${STGCN_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/STGCN}"
PYTHON="${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}"
GPU_ID="${GPU_ID:-1}"
SMOKE_DIR="$EXPERIMENT_DIR/smoke_multifeature_v1"
RAW_DIR="$EXPERIMENT_DIR/raw"
NET="$SNAPSHOT_DIR/data/maps/sumo/generated/network/TotalMap_20.signals.net.xml"

export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"
[[ ! -e "$SMOKE_DIR" ]] || { echo "smoke directory already exists: $SMOKE_DIR" >&2; exit 2; }
mkdir -p "$SMOKE_DIR"

"$PYTHON" - "$EXPERIMENT_DIR/collection_manifest_prediction_v1.tsv" "$SMOKE_DIR/manifest.json" <<'PY'
import csv, json, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8"), delimiter="\t"))
want = ("train", "validation", "test_in_distribution", "test_extrapolation")
selected = []
for split in want:
    selected.append(next(row for row in rows if row["split"] == split))
json.dump({"episodes": [{"id": row["id"], "split": row["split"], "demand_scale": float(row["demand_scale"]), "seed": int(row["seed"]), "file": f'{row["id"]}_5s_lanes.csv'} for row in selected]}, open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PY

TRAIN=""
ARGS=()
while IFS=$'\t' read -r split input; do
  [[ "$split" == train ]] && TRAIN="$input"
  ARGS+=(--input "$input")
done < <("$PYTHON" - "$SMOKE_DIR/manifest.json" "$RAW_DIR" <<'PY'
import json, sys
items = json.load(open(sys.argv[1], encoding="utf-8"))["episodes"]
for item in items:
    print(item["split"], f'{sys.argv[2]}/{item["file"]}', sep="\t")
PY
)
[[ -n "$TRAIN" ]] || { echo "smoke manifest has no train episode" >&2; exit 2; }

cd "$PROJECT_DIR"
"$PYTHON" -m algorithms.prediction.archive.legacy.filter_active_lanes --train-input "$TRAIN" "${ARGS[@]}" \
  --output-dir "$SMOKE_DIR/filtered" --min-active-samples 3 --min-active-ratio 0.01
"$PYTHON" -m algorithms.prediction.prepare_stgcn_episode_dataset \
  --manifest "$SMOKE_DIR/manifest.json" --input-dir "$SMOKE_DIR/filtered" --output-dir "$SMOKE_DIR/tensors" --net "$NET" \
  --target vehicle_count --feature vehicle_count --feature halting_count --feature mean_speed --feature occupancy --n-his 12 --n-pred 12
"$PYTHON" -m algorithms.prediction.evaluate_stage1_baselines --dataset-dir "$SMOKE_DIR/tensors" --output "$SMOKE_DIR/baselines.csv"
"$PYTHON" -m algorithms.prediction.train_xgboost_stage1 --dataset-dir "$SMOKE_DIR/tensors" --output-dir "$SMOKE_DIR/xgboost" \
  --max-train-rows 2000 --n-estimators 2 --max-depth 3 --n-jobs 2 --seed 42
CUDA_VISIBLE_DEVICES="$GPU_ID" "$PYTHON" -m algorithms.prediction.train_stgcn_stage1 --dataset-dir "$SMOKE_DIR/tensors" \
  --stgcn-root "$STGCN_DIR" --output-dir "$SMOKE_DIR/stgcn" --epochs 1 --patience 1 --batch-size 32 --seed 42 --horizon-step 12
[[ -s "$SMOKE_DIR/xgboost/metrics.json" && -s "$SMOKE_DIR/stgcn/metrics.json" ]]
echo "smoke_completed=$SMOKE_DIR"
