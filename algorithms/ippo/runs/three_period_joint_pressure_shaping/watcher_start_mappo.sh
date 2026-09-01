#!/usr/bin/env bash
set -u

BASE=/home/ubt/devdata/gsb/citypulse-v2x-sim
IPPO_LOG=$BASE/algorithms/ippo/runs/three_period_joint_pressure_shaping/full_900s/train.log
MARKER=$BASE/algorithms/ippo/runs/three_period_joint_pressure_shaping/full_900s/watcher.marker
MAPPO_RUN=$BASE/algorithms/mappo/runs/three_period_joint_pressure_shaping/full_900s
MAPPO_LOG=$MAPPO_RUN/train.log
MAPPO_SAVE=$MAPPO_RUN/mappo_3period_900s_480ep.pt

echo "watcher_started $(date)" >> "$MARKER"
while true; do
  if grep -q "并行训练完成: episodes=480" "$IPPO_LOG" 2>/dev/null; then
    echo "IPPO_DONE $(date)" >> "$MARKER"
    break
  fi
  if ! pgrep -f "parallel_train --episodes 480" >/dev/null 2>&1; then
    echo "IPPO_NOT_RUNNING $(date)" >> "$MARKER"
    exit 1
  fi
  sleep 60
done

mkdir -p "$MAPPO_RUN"
cd "$BASE"
echo "MAPPO_START $(date)" >> "$MARKER"
env PYTHONPATH=$BASE SUMO_HOME=/usr/share/sumo /home/ubt/miniconda3/envs/citypulse/bin/python -m algorithms.mappo.train \
  --model-version cooperative_joint_v1 --critic-scope global --intersections 20 \
  --episodes 480 --workers 6 --duration 900 --base-seed 95501 \
  --periods morning_peak off_peak evening_peak --checkpoint-every 12 \
  --pressure-shaping --save "$MAPPO_SAVE" >> "$MAPPO_LOG" 2>&1
status=$?
echo "MAPPO_EXIT status=$status $(date)" >> "$MARKER"
exit $status
