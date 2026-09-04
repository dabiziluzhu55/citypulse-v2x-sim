#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

copilot_require_curl

all_ok=1
if copilot_run_preflight; then
  :
else
  all_ok=0
fi

QWEN_MODELS_URL="$(copilot_qwen_models_url)"
if copilot_probe "${QWEN_MODELS_URL}"; then
  if copilot_managed_pid "Qwen" "${CITYPULSE_QWEN_PID_FILE}" || [[ "${CITYPULSE_ALLOW_UNMANAGED_SERVICES}" == "1" ]]; then
    echo "[OK] Qwen endpoint: ${QWEN_MODELS_URL}"
  else
    echo "[FAIL] Qwen responds but is unmanaged or has a stale PID file: ${QWEN_MODELS_URL}"
    all_ok=0
  fi
else
  echo "[FAIL] Qwen endpoint unavailable: ${QWEN_MODELS_URL}"
  all_ok=0
fi

QWEN_HEALTH_URL="$(copilot_qwen_health_url)"
if copilot_probe "${QWEN_HEALTH_URL}"; then
  echo "[OK] Qwen health: ${QWEN_HEALTH_URL}"
else
  echo "[WARN] Qwen health endpoint unavailable: ${QWEN_HEALTH_URL}"
fi

BACKEND_HEALTH_URL="$(copilot_backend_health_url)"
if copilot_probe "${BACKEND_HEALTH_URL}"; then
  if copilot_managed_pid "Backend" "${CITYPULSE_BACKEND_PID_FILE}" || [[ "${CITYPULSE_ALLOW_UNMANAGED_SERVICES}" == "1" ]]; then
    echo "[OK] Backend endpoint: ${BACKEND_HEALTH_URL}"
  else
    echo "[FAIL] Backend responds but is unmanaged or has a stale PID file: ${BACKEND_HEALTH_URL}"
    all_ok=0
  fi
else
  echo "[FAIL] Backend endpoint unavailable: ${BACKEND_HEALTH_URL}"
  all_ok=0
fi

if (( all_ok == 1 )); then
  echo "[OK] CityPulse Copilot is ready (Qwen + traffic RAG + standards RAG + Backend)."
  exit 0
fi

echo "[FAIL] CityPulse Copilot is not ready; fix the failed checks before testing." >&2
exit 1
