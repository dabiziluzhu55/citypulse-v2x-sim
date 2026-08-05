#!/usr/bin/env bash
set -euo pipefail

# CPU-only preparation for the TLS100 junction-level prediction task.
PROJECT_DIR=${PROJECT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim}
SNAPSHOT_DIR=${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}
EXPERIMENT_DIR=${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}
PYTHON=${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}

BASE_DIR="$EXPERIMENT_DIR/tls100_intersection_v1"
SOURCE_RAW_DIR="$EXPERIMENT_DIR/raw"
RAW_DIR="$BASE_DIR/raw"
REPORT_DIR="$BASE_DIR/graph_report"
PREFLIGHT_DIR="$BASE_DIR/preflight"
STATUS="$BASE_DIR/aggregation_status.tsv"
MANIFEST_TSV="$EXPERIMENT_DIR/collection_manifest_prediction_v1.tsv"
EPISODE_MANIFEST="$BASE_DIR/episode_manifest.json"
TLS_MANIFEST="$BASE_DIR/tls100_junction_manifest.json"
NET="$SNAPSHOT_DIR/data/maps/sumo/generated/network/TotalMap_20.signals.net.xml"
ADJACENCY="$BASE_DIR/adjacency.npz"

export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_DIR"

require_file() {
  [[ -s "$1" ]] || { echo "missing or empty: $1" >&2; exit 2; }
}

require_file "$MANIFEST_TSV"
require_file "$NET"
require_file "$SOURCE_RAW_DIR"

mkdir -p "$BASE_DIR" "$RAW_DIR" "$REPORT_DIR" "$PREFLIGHT_DIR"
if [[ ! -s "$STATUS" ]]; then
  printf 'utc\tid\tstate\n' > "$STATUS"
fi

cd "$PROJECT_DIR"
if [[ ! -s "$TLS_MANIFEST" ]]; then
  [[ ! -e "$TLS_MANIFEST.partial" ]] || { echo "partial TLS manifest exists: $TLS_MANIFEST.partial" >&2; exit 2; }
  "$PYTHON" -m algorithms.prediction.build_tls100_junction_manifest     --net "$NET" --output "$TLS_MANIFEST" > "$BASE_DIR/tls100_manifest.log"
else
  require_file "$TLS_MANIFEST"
fi

if [[ ! -s "$ADJACENCY" ]]; then
  [[ ! -e "$ADJACENCY.partial.npz" ]] || { echo "partial adjacency exists: $ADJACENCY.partial.npz" >&2; exit 2; }
  "$PYTHON" -m algorithms.prediction.build_tls100_junction_adjacency     --tls-manifest "$TLS_MANIFEST" --net "$NET" --output "$ADJACENCY"     --report-dir "$REPORT_DIR" > "$BASE_DIR/adjacency.log"
else
  require_file "$ADJACENCY"
  require_file "$REPORT_DIR/tls100_junction_adjacency_summary.json"
fi

if [[ ! -s "$EPISODE_MANIFEST" ]]; then
  [[ ! -e "$EPISODE_MANIFEST.partial" ]] || { echo "partial episode manifest exists: $EPISODE_MANIFEST.partial" >&2; exit 2; }
  "$PYTHON" - "$MANIFEST_TSV" "$EPISODE_MANIFEST" "$SOURCE_RAW_DIR" <<'PY'
import csv
import json
import sys
from collections import Counter
from pathlib import Path

source_manifest, output, source_dir = map(Path, sys.argv[1:4])
rows = list(csv.DictReader(source_manifest.open(encoding="utf-8"), delimiter="\t"))
if len(rows) != 30:
    raise SystemExit(f"expected exactly 30 episodes, found {len(rows)}")
required = {"id", "split", "demand_scale", "seed"}
if any(required - set(row) for row in rows):
    raise SystemExit("collection manifest lacks required episode fields")
episodes = []
for row in rows:
    source_file = f"{row['id']}_5s_lanes.csv"
    if not (source_dir / source_file).is_file():
        raise SystemExit(f"missing source snapshot: {source_dir / source_file}")
    episodes.append(
        {
            "id": row["id"],
            "split": row["split"],
            "demand_scale": float(row["demand_scale"]),
            "seed": int(row["seed"]),
            "source_file": source_file,
            "file": f"{row['id']}_5s_intersections.csv",
        }
    )
payload = {
    "schema_version": 1,
    "node_definition": "SUMO traffic_light junction",
    "source_manifest": str(source_manifest.resolve()),
    "source_dir": str(source_dir.resolve()),
    "episode_count": len(episodes),
    "split_counts": dict(sorted(Counter(item["split"] for item in episodes).items())),
    "episodes": episodes,
}
output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload["split_counts"], ensure_ascii=False))
PY
else
  require_file "$EPISODE_MANIFEST"
fi

verify_aggregate() {
  "$PYTHON" - "$1" "$2" <<'PY'
import csv
import json
import sys
from pathlib import Path

output, metadata = map(Path, sys.argv[1:3])
if not output.is_file() or not metadata.is_file():
    raise SystemExit(f"missing aggregate pair: {output}, {metadata}")
summary = json.loads(metadata.read_text(encoding="utf-8"))
if summary.get("status") != "aggregated":
    raise SystemExit(f"aggregate is not complete: {metadata}")
if summary.get("node_count") != 100 or summary.get("snapshot_count") != 721:
    raise SystemExit(f"unexpected TLS100 shape in {metadata}: {summary}")
expected_rows = 721 * 100
with output.open(newline="", encoding="utf-8") as handle:
    actual_rows = sum(1 for _ in handle) - 1
if actual_rows != expected_rows or summary.get("output_rows") != expected_rows:
    raise SystemExit(
        f"expected {expected_rows} aggregate rows, found {actual_rows}: {output}"
    )
PY
}

IFS=$'\t' read -r FIRST_ID FIRST_SOURCE_FILE FIRST_OUTPUT_FILE < <(
  "$PYTHON" - "$EPISODE_MANIFEST" <<'PY'
import json
import sys

episodes = json.load(open(sys.argv[1], encoding="utf-8"))["episodes"]
for item in episodes:
    if item["split"] == "train":
        print(item["id"], item["source_file"], item["file"], sep="\t")
        break
else:
    raise SystemExit("episode manifest has no train episode")
PY
)

first_source="$SOURCE_RAW_DIR/$FIRST_SOURCE_FILE"
first_output="$RAW_DIR/$FIRST_OUTPUT_FILE"
if [[ -s "$first_output" && -s "$first_output.metadata.json" ]]; then
  verify_aggregate "$first_output" "$first_output.metadata.json"
elif [[ -e "$first_output" || -e "$first_output.metadata.json" || -e "$first_output.partial" || -e "$first_output.metadata.json.partial" ]]; then
  echo "first aggregate has incomplete artefacts; refusing to overwrite: $first_output" >&2
  exit 2
else
  printf '%s\t%s\tstarted\n' "$(date -u +%FT%TZ)" "$FIRST_ID" >> "$STATUS"
  "$PYTHON" -m algorithms.prediction.aggregate_tls100_junction_snapshots     --input "$first_source" --tls-manifest "$TLS_MANIFEST"     --output "$first_output" --metadata "$first_output.metadata.json"     > "$PREFLIGHT_DIR/$FIRST_ID.json"
  verify_aggregate "$first_output" "$first_output.metadata.json"
  printf '%s\t%s\tcompleted\n' "$(date -u +%FT%TZ)" "$FIRST_ID" >> "$STATUS"
fi

while IFS=$'\t' read -r id split source_file output_file; do
  source="$SOURCE_RAW_DIR/$source_file"
  output="$RAW_DIR/$output_file"
  if [[ -s "$output" && -s "$output.metadata.json" ]]; then
    verify_aggregate "$output" "$output.metadata.json"
    continue
  fi
  if [[ -e "$output" || -e "$output.metadata.json" || -e "$output.partial" || -e "$output.metadata.json.partial" ]]; then
    echo "aggregate has incomplete artefacts; refusing to overwrite: $output" >&2
    exit 2
  fi
  printf '%s\t%s\tstarted\n' "$(date -u +%FT%TZ)" "$id" >> "$STATUS"
  "$PYTHON" -m algorithms.prediction.aggregate_tls100_junction_snapshots     --input "$source" --tls-manifest "$TLS_MANIFEST"     --output "$output" --metadata "$output.metadata.json"     >> "$BASE_DIR/aggregate.log" 2>&1
  verify_aggregate "$output" "$output.metadata.json"
  printf '%s\t%s\tcompleted\n' "$(date -u +%FT%TZ)" "$id" >> "$STATUS"
done < <(
  "$PYTHON" - "$EPISODE_MANIFEST" <<'PY'
import json
import sys

for item in json.load(open(sys.argv[1], encoding="utf-8"))["episodes"]:
    print(item["id"], item["split"], item["source_file"], item["file"], sep="\t")
PY
)

"$PYTHON" - "$EPISODE_MANIFEST" "$RAW_DIR" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
raw_dir = Path(sys.argv[2])
for item in manifest["episodes"]:
    output = raw_dir / item["file"]
    metadata = Path(str(output) + ".metadata.json")
    if not output.is_file() or not metadata.is_file():
        raise SystemExit(f"missing completed aggregate: {output}")
print(f"aggregated_episodes={len(manifest['episodes'])}")
print(f"split_counts={manifest['split_counts']}")
PY
