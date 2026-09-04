#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

copilot_require_curl
copilot_require_qwen_python
copilot_run_preflight

if [[ ! -f "${CITYPULSE_QWEN_SERVER_SCRIPT}" ]]; then
  echo "[ERROR] Qwen server script is missing: ${CITYPULSE_QWEN_SERVER_SCRIPT}" >&2
  exit 1
fi
if [[ -z "${CITYPULSE_QWEN_MODEL_PATH}" ]]; then
  echo "[ERROR] CITYPULSE_QWEN_MODEL_PATH is not configured." >&2
  exit 1
fi

mkdir -p "${CITYPULSE_RUNTIME_DIR}"

QWEN_MODELS_URL="$(copilot_qwen_models_url)"
qwen_pid=""
if [[ -f "${CITYPULSE_QWEN_PID_FILE}" ]]; then
  candidate_pid="$(tr -d '[:space:]' < "${CITYPULSE_QWEN_PID_FILE}")"
  if copilot_pid_matches "Qwen" "${candidate_pid}"; then
    qwen_pid="${candidate_pid}"
  elif [[ "${candidate_pid}" =~ ^[0-9]+$ ]] && kill -0 "${candidate_pid}" >/dev/null 2>&1; then
    echo "[ERROR] Qwen PID file points to another live process: ${CITYPULSE_QWEN_PID_FILE}" >&2
    exit 1
  else
    rm -f "${CITYPULSE_QWEN_PID_FILE}"
  fi
fi

if copilot_probe "${QWEN_MODELS_URL}"; then
  if [[ -n "${qwen_pid}" || "${CITYPULSE_ALLOW_UNMANAGED_SERVICES}" == "1" ]]; then
    echo "[OK] Qwen is already ready: ${QWEN_MODELS_URL}"
    if [[ -z "${qwen_pid}" ]]; then
      echo "[WARN] Qwen is unmanaged; stop_copilot.sh will not stop it." >&2
    fi
    exit 0
  fi
  echo "[ERROR] Qwen endpoint is already occupied by an unmanaged process: ${QWEN_MODELS_URL}" >&2
  echo "[ERROR] Stop/verify that process, or set CITYPULSE_ALLOW_UNMANAGED_SERVICES=1 deliberately." >&2
  exit 1
fi

if [[ -z "${qwen_pid}" ]]; then
  echo "[INFO] Starting Qwen model service..."
  nohup "${CITYPULSE_QWEN_PYTHON}" "${CITYPULSE_QWEN_SERVER_SCRIPT}" \
    --model-path "${CITYPULSE_QWEN_MODEL_PATH}" \
    --served-model-name "${CITYPULSE_QWEN_MODEL}" \
    --host "${CITYPULSE_QWEN_HOST}" \
    --port "${CITYPULSE_QWEN_PORT}" \
    --max-input-tokens "${CITYPULSE_QWEN_MAX_INPUT_TOKENS}" \
    >"${CITYPULSE_QWEN_LOG_FILE}" 2>&1 < /dev/null &
  qwen_pid="$!"
  printf '%s\n' "${qwen_pid}" > "${CITYPULSE_QWEN_PID_FILE}"
else
  echo "[INFO] Qwen process ${qwen_pid} is already loading; waiting for readiness..."
fi

if copilot_wait_for_url "${QWEN_MODELS_URL}" "${CITYPULSE_QWEN_START_TIMEOUT_SECONDS}" "${CITYPULSE_QWEN_PID_FILE}"; then
  echo "[OK] Qwen is ready: ${QWEN_MODELS_URL}"
else
  wait_status=$?
  if [[ "${wait_status}" -eq 2 ]]; then
    echo "[ERROR] Qwen process exited before becoming ready." >&2
  else
    echo "[ERROR] Qwen did not become ready within ${CITYPULSE_QWEN_START_TIMEOUT_SECONDS}s." >&2
  fi
  copilot_show_log_tail "${CITYPULSE_QWEN_LOG_FILE}"
  exit 1
fi
