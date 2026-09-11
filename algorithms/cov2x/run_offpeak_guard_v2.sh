#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

source /home/kemove/anaconda3/etc/profile.d/conda.sh
conda activate v2x-ai-py310

export SUMO_HOME=/usr/share/sumo
export PATH="/usr/share/sumo/bin:${PATH}"
export COV2X_TEMPORARY_SPEED_CAP_V1=1
export COV2X_LOCAL_CREDIT_V1=1
export COV2X_ACTOR_UPDATE_SCHEDULE_ID=temporary_base_relative_three_scope_latin_gain_1_3_2_3_1_x22_v1
export COV2X_OFFPEAK_GUARD_V2=1
export COV2X_OFFPEAK_GUARD_PERIODS=off_peak
export COV2X_OFFPEAK_ROAD_FALLBACK=strong_mp
export COV2X_OFFPEAK_GUARD_MAX_GENERATION=128
export COV2X_EPISODE_RESEED_AFTER_RESTORE=1

RUN_DIR=algorithms/cov2x/runs/cov2x_offpeak_guard_v2
PARENT=traffic_control/cov2x/models/cov2x_g30_temporary_cap_u24.pt
mkdir -p "$RUN_DIR/checkpoints"

exec python -u -m algorithms.cov2x.train \
  --mode train \
  --signal-mode learned \
  --cloud-mode learned \
  --vehicle-mode learned \
  --preset xiongan_20 \
  --duration 900 \
  --balanced-updates 30 \
  --checkpoint-every 5 \
  --seed 24501 \
  --resume "$PARENT" \
  --checkpoint-dir "$RUN_DIR/checkpoints" \
  --save "$RUN_DIR/cov2x_offpeak_guard_v2_final.pt" \
  --log "$RUN_DIR/episodes.jsonl"
