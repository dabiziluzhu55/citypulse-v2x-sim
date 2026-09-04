#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

mkdir -p "${CITYPULSE_RUNTIME_DIR}"

stop_pid_file() {
  local label="$1"
  local pid_file="$2"
  if [[ ! -f "${pid_file}" ]]; then
    echo "[INFO] ${label} is not managed by this launcher."
    return 0
  fi

  local pid
  pid="$(tr -d '[:space:]' < "${pid_file}")"
  if [[ ! "${pid}" =~ ^[0-9]+$ ]]; then
    echo "[WARN] Removing invalid ${label} PID file: ${pid_file}"
    rm -f "${pid_file}"
    return 0
  fi
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[INFO] ${label} process ${pid} is already stopped."
    rm -f "${pid_file}"
    return 0
  fi
  if ! copilot_pid_matches "${label}" "${pid}"; then
    echo "[ERROR] Refusing to stop ${label}: PID ${pid} does not match the expected launcher command." >&2
    return 1
  fi

  echo "[INFO] Stopping ${label} process ${pid}..."
  kill "${pid}"
  for _ in $(seq 1 30); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${pid_file}"
      echo "[OK] ${label} stopped."
      return 0
    fi
    sleep 1
  done
  echo "[WARN] ${label} did not exit after 30s; it was not force-killed." >&2
  return 1
}

stop_failed=0
if ! stop_pid_file "Backend" "${CITYPULSE_BACKEND_PID_FILE}"; then
  stop_failed=1
fi
if ! stop_pid_file "Qwen" "${CITYPULSE_QWEN_PID_FILE}"; then
  stop_failed=1
fi

if (( stop_failed == 0 )); then
  echo "[OK] Managed Copilot processes stopped."
else
  exit 1
fi
