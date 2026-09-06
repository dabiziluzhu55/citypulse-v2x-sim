#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

copilot_require_curl
copilot_run_preflight

QWEN_MODELS_URL="$(copilot_qwen_models_url)"
if ! copilot_probe "${QWEN_MODELS_URL}"; then
  echo "[ERROR] Qwen is not ready at ${QWEN_MODELS_URL}; start it before Backend." >&2
  exit 1
fi

if [[ ! -f "${CITYPULSE_REPO_ROOT}/backend/app/main.py" ]]; then
  echo "[ERROR] Backend entrypoint is missing under ${CITYPULSE_REPO_ROOT}." >&2
  exit 1
fi

mkdir -p "${CITYPULSE_RUNTIME_DIR}"
BACKEND_HEALTH_URL="$(copilot_backend_health_url)"
backend_pid=""
if [[ -f "${CITYPULSE_BACKEND_PID_FILE}" ]]; then
  candidate_pid="$(tr -d '[:space:]' < "${CITYPULSE_BACKEND_PID_FILE}")"
  if copilot_pid_matches "Backend" "${candidate_pid}"; then
    backend_pid="${candidate_pid}"
  elif [[ "${candidate_pid}" =~ ^[0-9]+$ ]] && kill -0 "${candidate_pid}" >/dev/null 2>&1; then
    echo "[ERROR] Backend PID file points to another live process: ${CITYPULSE_BACKEND_PID_FILE}" >&2
    exit 1
  else
    rm -f "${CITYPULSE_BACKEND_PID_FILE}"
  fi
fi

if copilot_probe "${BACKEND_HEALTH_URL}"; then
  if [[ -n "${backend_pid}" || "${CITYPULSE_ALLOW_UNMANAGED_SERVICES}" == "1" ]]; then
    echo "[OK] Backend is already ready: ${BACKEND_HEALTH_URL}"
    if [[ -z "${backend_pid}" ]]; then
      echo "[WARN] Backend is unmanaged; stop_copilot.sh will not stop it." >&2
    fi
    exit 0
  fi
  echo "[ERROR] Backend endpoint is already occupied by an unmanaged process: ${BACKEND_HEALTH_URL}" >&2
  echo "[ERROR] Stop/verify that process, or set CITYPULSE_ALLOW_UNMANAGED_SERVICES=1 deliberately." >&2
  exit 1
fi

if [[ -z "${backend_pid}" ]]; then
  echo "[INFO] Starting CityPulse Backend..."
  cd "${CITYPULSE_REPO_ROOT}"
  nohup "${CITYPULSE_PYTHON}" -m uvicorn backend.app.main:app \
    --host "${CITYPULSE_BACKEND_HOST}" \
    --port "${CITYPULSE_BACKEND_PORT}" \
    --workers 1 \
    >"${CITYPULSE_BACKEND_LOG_FILE}" 2>&1 < /dev/null &
  backend_pid="$!"
  printf '%s\n' "${backend_pid}" > "${CITYPULSE_BACKEND_PID_FILE}"
else
  echo "[INFO] Backend process ${backend_pid} is already starting; waiting for readiness..."
fi

if copilot_wait_for_url "${BACKEND_HEALTH_URL}" "${CITYPULSE_BACKEND_START_TIMEOUT_SECONDS}" "${CITYPULSE_BACKEND_PID_FILE}"; then
  echo "[OK] Backend is ready: ${BACKEND_HEALTH_URL}"
else
  wait_status=$?
  if [[ "${wait_status}" -eq 2 ]]; then
    echo "[ERROR] Backend process exited before becoming ready." >&2
  else
    echo "[ERROR] Backend did not become ready within ${CITYPULSE_BACKEND_START_TIMEOUT_SECONDS}s." >&2
  fi
  copilot_show_log_tail "${CITYPULSE_BACKEND_LOG_FILE}"
  exit 1
fi
