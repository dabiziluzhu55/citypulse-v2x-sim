#!/usr/bin/env bash
# Production CityPulse-Qwen vLLM launcher (no algorithms/ dependency).

set -euo pipefail

MODEL_DIR="${CITYPULSE_QWEN_MODEL_DIR:-/models/citypulse-qwen-v2-awq}"
HOST="${CITYPULSE_QWEN_HOST:-0.0.0.0}"
PORT="${CITYPULSE_QWEN_PORT:-8001}"
SERVED_NAME="${CITYPULSE_QWEN_SERVED_NAME:-traffic-qwen-v2}"

if [[ ! -d "${MODEL_DIR}" ]]; then
  echo "Model directory not found: ${MODEL_DIR}" >&2
  exit 1
fi

exec vllm serve "${MODEL_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --served-model-name "${SERVED_NAME}" \
  --quantization awq \
  --dtype auto \
  --max-model-len "${CITYPULSE_QWEN_MAX_MODEL_LEN:-8192}"
