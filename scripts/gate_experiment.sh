#!/bin/bash
# =============================================================
# V16→V17 结构门禁实验（gate_experiment.sh）
# 用途：验证 learned Top-K 架构切换冲击，通过后才可长训
# 运行位置：4090 服务器（~/devdata1/gsb/citypulse-v2x-sim）
# 编写：路云 | 2026-08-04 | 依据 controller.py _ppo_update 诊断
# =============================================================
set -u

# ---------- 参数 ----------
INTERSECTIONS=${INTERSECTIONS:-20}
WORKERS=${WORKERS:-4}
EPISODES=${EPISODES:-4}
TOP_K=${TOP_K:-5}
PERIOD=${PERIOD:-off_peak}
# V16 ep8 checkpoint（warm-start 源，同步前已备份）
WARM_START_CKPT=${WARM_START_CKPT:-"runs/coslight_parallel/checkpoints/model_ep8.pt"}
LOG_DIR="logs/gate_v17_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/train.log"
mkdir -p "$LOG_DIR"

# ---------- 判据阈值（通过区间） ----------
KL_MAX=1.0            # topology_probe_kl 上限
CHANGE_MIN=0.05       # topology_probe_argmax_change 下限（有变化）
CHANGE_MAX=0.90       # topology_probe_argmax_change 上限（未崩）
NONLOCAL_MIN=0.05     # selection_nonlocal 下限（真选了非本地路口）

echo "=== V17 结构门禁实验 ==="
echo "参数: ${INTERSECTIONS}路口 / ${WORKERS}worker / ${EPISODES}ep / top_k=${TOP_K} / ${PERIOD}"
echo "warm-start: $WARM_START_CKPT"
echo "日志: $LOG_FILE"

# ---------- 0. 负载检查（共用服务器礼仪） ----------
echo "--- nvidia-smi 检查 ---"
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv
read -r -p "GPU 有队友任务吗？确认空闲再继续 (y/N) " ans
[ "$ans" = "y" ] || { echo "中止"; exit 1; }

# ---------- 1. warm-start 检查点存在性 ----------
if [ ! -f "$WARM_START_CKPT" ]; then
  echo "ERROR: warm-start checkpoint 不存在: $WARM_START_CKPT"
  echo "先找 V16 ep8: find runs -name 'model_ep8.pt' 或 ls algorithms/coslight/checkpoints/"
  exit 1
fi

# ---------- 2. 启动门禁训练（后台 + 日志） ----------
echo "--- 启动训练 ---"
nohup python -u algorithms/coslight/parallel_train.py \
  --intersections "$INTERSECTIONS" \
  --workers "$WORKERS" \
  --episodes "$EPISODES" \
  --top-k "$TOP_K" \
  --period "$PERIOD" \
  --warm-start "$WARM_START_CKPT" \
  --save "runs/coslight_parallel/gate_v17_$(date +%Y%m%d_%H%M%S).pt" \
  > "$LOG_FILE" 2>&1 &
TRAIN_PID=$!
echo "训练 PID: $TRAIN_PID (日志: $LOG_FILE)"

# ---------- 3. 等待 PPO diagnostics 出现（最多 20 分钟） ----------
echo "--- 等待 PPO diagnostics（探针诊断）---"
DIAG_LINE=""
for i in $(seq 1 40); do
  DIAG_LINE=$(grep -m1 "PPO diagnostics" "$LOG_FILE" 2>/dev/null || true)
  [ -n "$DIAG_LINE" ] && break
  if ! kill -0 "$TRAIN_PID" 2>/dev/null; then
    echo "ERROR: 训练提前退出，看日志尾部："
    tail -20 "$LOG_FILE"
    exit 1
  fi
  sleep 30
done

if [ -z "$DIAG_LINE" ]; then
  echo "ERROR: 20 分钟内无 PPO diagnostics，训练可能卡住或参数错误"
  tail -30 "$LOG_FILE"
  kill "$TRAIN_PID" 2>/dev/null
  exit 1
fi

echo "--- 首条诊断 ---"
echo "$DIAG_LINE"

# ---------- 4. 收集全部诊断行，取最后一次（收敛后的稳态） ----------
# 等训练自然结束（4 episode 很快）或前 5 条诊断后
for i in $(seq 1 40); do
  kill -0 "$TRAIN_PID" 2>/dev/null || break
  sleep 30
done
if kill -0 "$TRAIN_PID" 2>/dev/null; then
  echo "警告: 训练未在预期时间内结束，取当前诊断"
  kill "$TRAIN_PID" 2>/dev/null
fi

LAST_DIAG=$(grep "PPO diagnostics" "$LOG_FILE" | tail -1)
echo "--- 末次诊断 ---"
echo "$LAST_DIAG"

# ---------- 5. 指标解析与判据 ----------
extract() { # $1=pattern $2=line（grep -oE；保留 / 分隔的多段数值，日志均为 %.6f 纯小数）
  echo "$2" | grep -oE "$1" | head -1 | grep -oE '[0-9.]+(/[0-9.]+)*' | head -1
}

TP_KL=$(extract 'topology_probe_kl/change=[0-9.]+/[0-9.]+' "$LAST_DIAG")
TP_KL_V=$(echo "$TP_KL" | cut -d'/' -f1)
TP_CHG_V=$(echo "$TP_KL" | cut -d'/' -f2)
NONLOCAL=$(extract 'selection_nonlocal/self/unique/reciprocal=[0-9.]+/[0-9.]+/[0-9.]+/[0-9.]+' "$LAST_DIAG" | cut -d'/' -f1)
SELF=$(extract 'selection_nonlocal/self/unique/reciprocal=[0-9.]+/[0-9.]+/[0-9.]+/[0-9.]+' "$LAST_DIAG" | cut -d'/' -f2)
CHURN=$(extract 'probe_topk_churn=[0-9.]+' "$LAST_DIAG")

echo ""
echo "========== 门禁判据结果 =========="
echo "topology_probe_kl        = ${TP_KL_V:-N/A}   (要求 < $KL_MAX)"
echo "topology_argmax_change   = ${TP_CHG_V:-N/A}  (要求 $CHANGE_MIN ~ $CHANGE_MAX)"
echo "selection_nonlocal       = ${NONLOCAL:-N/A}  (要求 > $NONLOCAL_MIN)"
echo "selection_self           = ${SELF:-N/A}      (要求 < 0.95)"
echo "probe_topk_churn         = ${CHURN:-N/A}     (要求 > 0)"
echo "==================================="

PASS=1
[ -z "$TP_KL_V" ] && { echo "✗ 无法解析 topology_probe_kl"; PASS=0; }
awk -v k="$TP_KL_V" -v m="$KL_MAX" 'BEGIN{exit !(k<m)}' || { echo "✗ KL 超限: $TP_KL_V >= $KL_MAX"; PASS=0; }
awk -v c="$TP_CHG_V" -v lo="$CHANGE_MIN" -v hi="$CHANGE_MAX" 'BEGIN{exit !(c>=lo && c<=hi)}' || { echo "✗ 动作改变率越界: $TP_CHG_V 不在 [$CHANGE_MIN, $CHANGE_MAX]"; PASS=0; }
awk -v n="$NONLOCAL" -v m="$NONLOCAL_MIN" 'BEGIN{exit !(n>m)}' || { echo "✗ 非本地选择不足: $NONLOCAL <= $NONLOCAL_MIN（Top-K 未生效）"; PASS=0; }

if [ "$PASS" = "1" ]; then
  echo ""
  echo "✅ 门禁通过：架构切换冲击可控，Top-K 协作在选择非本地路口。"
  echo "下一步：长训 --episodes 200（同 warm-start），完成后按金标准 4-seed 评估。"
  echo "完整日志: $LOG_FILE"
  exit 0
else
  echo ""
  echo "❌ 门禁失败：见上方 ✗ 项。"
  echo "处置建议："
  echo "  1. 动作改变率 > 0.9 → 架构切换打崩策略，检查 warm-start 兼容性（controller.py 542-551 开关）"
  echo "  2. 改变率 < 0.05 且 nonlocal 低 → Top-K 未生效，检查 K/打分网络（score/target/source projection）"
  echo "  3. 训练异常 → 看 $LOG_FILE 尾部"
  echo "V16 ep8 部署不受影响（金标准模型未动）。"
  exit 1
fi
