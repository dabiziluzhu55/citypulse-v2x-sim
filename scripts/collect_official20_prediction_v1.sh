#!/usr/bin/env bash
set -euo pipefail

# Prediction V1: fixed-time traffic-state forecasting.  The model target is
# vehicle_count, while the raw CSVs retain all four lane metrics for later
# multivariate forecasting, event-rule calibration, and quality analysis.
# The source snapshot is isolated from the server's main repository.

SNAPSHOT_DIR="${SNAPSHOT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970}"
EXPERIMENT_DIR="${EXPERIMENT_DIR:-/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1}"
PYTHON="${PYTHON:-/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python}"
SUMO_HOME="${SUMO_HOME:-/usr/share/sumo}"
EXPECTED_SOURCE_COMMIT="1686970a77bbb12927a7dd6f83e3ad00f20198e5"

COLLECTOR="$EXPERIMENT_DIR/tools/collect_standard_sumo_snapshots.py"
MANIFEST="$EXPERIMENT_DIR/collection_manifest_prediction_v1.tsv"
STATUS="$EXPERIMENT_DIR/collection_status_prediction_v1.tsv"
RAW_DIR="$EXPERIMENT_DIR/raw"

export SUMO_HOME
export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
export PYTHONPATH="$SUMO_HOME/tools${PYTHONPATH:+:$PYTHONPATH}"

[[ "$(git -C "$SNAPSHOT_DIR" rev-parse HEAD)" == "$EXPECTED_SOURCE_COMMIT" ]] || {
  echo "Unexpected SUMO source commit." >&2
  exit 2
}
[[ -f "$COLLECTOR" ]] || { echo "Collector not found: $COLLECTOR" >&2; exit 2; }
mkdir -p "$RAW_DIR" "$EXPERIMENT_DIR/logs"

if [[ ! -s "$MANIFEST" ]]; then
  printf 'id\tsplit\tperiod\tdemand_scale\tseed\tduration_seconds\tsnapshot_interval_seconds\n' > "$MANIFEST"
  add_episodes() {
    local split="$1" scales="$2" seeds="$3"
    local scale seed period id
    for scale in $scales; do
      for seed in $seeds; do
        for period in morning_peak off_peak evening_peak; do
          id="${split}_${period}_scale${scale}_seed${seed}"
          printf '%s\t%s\t%s\t%s\t%s\t3600\t5\n' "$id" "$split" "$period" "$scale" "$seed" >> "$MANIFEST"
        done
      done
    done
  }
  add_episodes train '0.8 1.0 1.2' '201 202'
  add_episodes validation '1.0' '203'
  add_episodes test_in_distribution '1.0' '204'
  add_episodes test_extrapolation '0.7 1.3' '204'
fi

if [[ ! -s "$STATUS" ]]; then
  printf 'utc\tid\tstate\tdetail\n' > "$STATUS"
fi

wait_for_idle_sumo() {
  local episode_id="$1"
  while pgrep -f '/usr/share/sumo/bin/sumo|sumo-gui' >/dev/null; do
    printf '%s\t%s\twaiting_external_sumo\tforeign SUMO process detected\n' \
      "$(date -u +%FT%TZ)" "$episode_id" >> "$STATUS"
    sleep 60
  done
}

while IFS=$'\t' read -r id split period scale seed duration interval; do
  [[ "$id" == 'id' ]] && continue
  output="$RAW_DIR/${id}_5s_lanes.csv"
  partial="${output}.partial"
  if [[ -s "$output" && ! -e "$partial" ]]; then
    printf '%s\t%s\tskipped_complete\t%s\n' "$(date -u +%FT%TZ)" "$id" "$output" >> "$STATUS"
    continue
  fi
  if [[ -e "$partial" ]]; then
    printf '%s\t%s\tblocked_partial\t%s\n' "$(date -u +%FT%TZ)" "$id" "$partial" >> "$STATUS"
    exit 2
  fi

  sumocfg="$SNAPSHOT_DIR/data/maps/sumo/generated/traffic/global/$period/simulation.sumocfg"
  [[ -f "$sumocfg" ]] || { echo "Missing scenario: $sumocfg" >&2; exit 2; }
  wait_for_idle_sumo "$id"
  printf '%s\t%s\tstarted\t%s\n' "$(date -u +%FT%TZ)" "$id" "$sumocfg" >> "$STATUS"
  set +e
  timeout 7200 "$PYTHON" "$COLLECTOR" \
    --sumocfg "$sumocfg" --output "$output" --snapshot-interval "$interval" \
    --duration "$duration" --seed "$seed" --demand-scale "$scale" \
    --sumo-binary /usr/share/sumo/bin/sumo
  result=$?
  set -e
  if [[ $result -ne 0 || ! -s "$output" || -e "$partial" ]]; then
    printf '%s\t%s\tfailed\texit=%s output=%s partial=%s\n' \
      "$(date -u +%FT%TZ)" "$id" "$result" \
      "$([[ -s "$output" ]] && echo yes || echo no)" \
      "$([[ -e "$partial" ]] && echo yes || echo no)" >> "$STATUS"
    exit 1
  fi
  printf '%s\t%s\tcompleted\t%s\n' "$(date -u +%FT%TZ)" "$id" "$output" >> "$STATUS"
done < "$MANIFEST"

printf '%s\tbatch\tcompleted\tall 30 prediction-v1 episodes complete\n' "$(date -u +%FT%TZ)" >> "$STATUS"
