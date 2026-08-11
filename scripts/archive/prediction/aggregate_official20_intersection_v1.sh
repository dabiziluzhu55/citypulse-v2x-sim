#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}"
EXPERIMENT_DIR="${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}"
PYTHON="${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}"
OUT="$EXPERIMENT_DIR/intersection20_v1"
mkdir -p "$OUT/raw"
status="$OUT/status.tsv"; [[ -s "$status" ]] || printf 'utc\tid\tstate\n' > "$status"
"$PYTHON" -m algorithms.prediction.archive.aggregation.build_intersection20_adjacency --tls-manifest "$SNAPSHOT_DIR/data/maps/sumo/generated/manifests/tls_manifest.json" --intersections "$SNAPSHOT_DIR/data/maps/sumo/TotalMap_20.intersections.json" --output "$OUT/adjacency.npz" --neighbors 3 > "$OUT/adjacency.log"
tail -n +2 "$EXPERIMENT_DIR/collection_manifest_prediction_v1.tsv" | while IFS=$'\t' read -r id split period scale seed duration interval; do
  out="$OUT/raw/${id}_5s_intersections.csv"
  if [[ -s "$out" ]]; then continue; fi
  printf '%s\t%s\tstarted\n' "$(date -u +%FT%TZ)" "$id" >> "$status"
  "$PYTHON" -m algorithms.prediction.archive.aggregation.aggregate_intersection20_snapshots --input "$EXPERIMENT_DIR/raw/${id}_5s_lanes.csv" --output "$out" --tls-manifest "$SNAPSHOT_DIR/data/maps/sumo/generated/manifests/tls_manifest.json" >> "$OUT/aggregate.log" 2>&1
  printf '%s\t%s\tcompleted\n' "$(date -u +%FT%TZ)" "$id" >> "$status"
done
printf '%s\tbatch\tcompleted\n' "$(date -u +%FT%TZ)" >> "$status"
