#!/usr/bin/env bash
# Shared configuration and checks for the CityPulse Copilot services.

set -euo pipefail

COPILOT_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CITYPULSE_REPO_ROOT="$(cd -- "${COPILOT_SCRIPT_DIR}/../.." && pwd)"
CITYPULSE_ENV_FILE="${CITYPULSE_ENV_FILE:-${COPILOT_SCRIPT_DIR}/copilot.env}"
if [[ "${CITYPULSE_ENV_FILE}" != /* ]]; then
  CITYPULSE_ENV_FILE="${CITYPULSE_REPO_ROOT}/${CITYPULSE_ENV_FILE}"
fi

if [[ -f "${CITYPULSE_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${CITYPULSE_ENV_FILE}"
  set +a
fi

export CITYPULSE_REPO_ROOT
export CITYPULSE_ENV_FILE
export CITYPULSE_PYTHON="${CITYPULSE_PYTHON:-python3}"
export CITYPULSE_QWEN_PYTHON="${CITYPULSE_QWEN_PYTHON:-${CITYPULSE_PYTHON}}"
export CITYPULSE_QWEN_SERVER_SCRIPT="${CITYPULSE_QWEN_SERVER_SCRIPT:-${CITYPULSE_REPO_ROOT}/scripts/llm/qwen_transformers_server.py}"
if [[ "${CITYPULSE_QWEN_SERVER_SCRIPT}" != /* ]]; then
  CITYPULSE_QWEN_SERVER_SCRIPT="${CITYPULSE_REPO_ROOT}/${CITYPULSE_QWEN_SERVER_SCRIPT}"
fi
export CITYPULSE_QWEN_SERVER_SCRIPT
export CITYPULSE_QWEN_MODEL_PATH="${CITYPULSE_QWEN_MODEL_PATH:-}"
export CITYPULSE_QWEN_MODEL="${CITYPULSE_QWEN_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
export CITYPULSE_QWEN_HOST="${CITYPULSE_QWEN_HOST:-127.0.0.1}"
export CITYPULSE_QWEN_PORT="${CITYPULSE_QWEN_PORT:-18000}"
export CITYPULSE_QWEN_BASE_URL="${CITYPULSE_QWEN_BASE_URL:-http://${CITYPULSE_QWEN_HOST}:${CITYPULSE_QWEN_PORT}/v1}"
export CITYPULSE_QWEN_MAX_INPUT_TOKENS="${CITYPULSE_QWEN_MAX_INPUT_TOKENS:-4096}"
export CITYPULSE_QWEN_START_TIMEOUT_SECONDS="${CITYPULSE_QWEN_START_TIMEOUT_SECONDS:-300}"

export CITYPULSE_BACKEND_HOST="${CITYPULSE_BACKEND_HOST:-127.0.0.1}"
export CITYPULSE_BACKEND_PORT="${CITYPULSE_BACKEND_PORT:-8000}"
export CITYPULSE_BACKEND_URL="${CITYPULSE_BACKEND_URL:-http://${CITYPULSE_BACKEND_HOST}:${CITYPULSE_BACKEND_PORT}}"
export CITYPULSE_BACKEND_START_TIMEOUT_SECONDS="${CITYPULSE_BACKEND_START_TIMEOUT_SECONDS:-60}"

export RAG_INDEX_DIR="${RAG_INDEX_DIR:-${CITYPULSE_REPO_ROOT}/outputs/rag/traffic_knowledge_chroma}"
export RAG_KNOWLEDGE_MANIFEST="${RAG_KNOWLEDGE_MANIFEST:-${CITYPULSE_REPO_ROOT}/traffic_knowledge/manifest.json}"
export RAG_KNOWLEDGE_CHUNKS="${RAG_KNOWLEDGE_CHUNKS:-${CITYPULSE_REPO_ROOT}/traffic_knowledge/build/chunks.jsonl}"
export RAG_EMBEDDING_MODEL="${RAG_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
export RAG_EMBEDDING_MODEL_PATH="${RAG_EMBEDDING_MODEL_PATH:-}"
export RAG_COLLECTION_NAME="${RAG_COLLECTION_NAME:-citypulse_traffic_knowledge}"
export RAG_STANDARDS_INDEX_DIR="${RAG_STANDARDS_INDEX_DIR:-}"
export RAG_STANDARDS_MANIFEST="${RAG_STANDARDS_MANIFEST:-}"
export RAG_STANDARDS_CHUNKS="${RAG_STANDARDS_CHUNKS:-}"
export RAG_STANDARDS_COLLECTION_NAME="${RAG_STANDARDS_COLLECTION_NAME:-citypulse_standards_policy}"
export RAG_EMBEDDING_DIMENSION="${RAG_EMBEDDING_DIMENSION:-1024}"

export SUMO_HOME="${SUMO_HOME:-}"
export PYTHONPATH="${CITYPULSE_REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

export CITYPULSE_RUNTIME_DIR="${CITYPULSE_RUNTIME_DIR:-${CITYPULSE_REPO_ROOT}/outputs/.copilot_runtime}"
if [[ "${CITYPULSE_RUNTIME_DIR}" != /* ]]; then
  CITYPULSE_RUNTIME_DIR="${CITYPULSE_REPO_ROOT}/${CITYPULSE_RUNTIME_DIR}"
fi
export CITYPULSE_RUNTIME_DIR
export CITYPULSE_QWEN_PID_FILE="${CITYPULSE_QWEN_PID_FILE:-${CITYPULSE_RUNTIME_DIR}/qwen.pid}"
export CITYPULSE_BACKEND_PID_FILE="${CITYPULSE_BACKEND_PID_FILE:-${CITYPULSE_RUNTIME_DIR}/backend.pid}"
export CITYPULSE_QWEN_LOG_FILE="${CITYPULSE_QWEN_LOG_FILE:-${CITYPULSE_RUNTIME_DIR}/qwen.log}"
export CITYPULSE_BACKEND_LOG_FILE="${CITYPULSE_BACKEND_LOG_FILE:-${CITYPULSE_RUNTIME_DIR}/backend.log}"
export CITYPULSE_HEALTH_TIMEOUT_SECONDS="${CITYPULSE_HEALTH_TIMEOUT_SECONDS:-3}"
export CITYPULSE_ALLOW_UNMANAGED_SERVICES="${CITYPULSE_ALLOW_UNMANAGED_SERVICES:-0}"

copilot_require_curl() {
  if ! command -v curl >/dev/null 2>&1; then
    echo "[ERROR] curl is required for service health checks." >&2
    return 1
  fi
}

copilot_require_python() {
  if [[ "${CITYPULSE_PYTHON}" == */* ]]; then
    if [[ ! -x "${CITYPULSE_PYTHON}" ]]; then
      echo "[ERROR] Python executable is not available: ${CITYPULSE_PYTHON}" >&2
      return 1
    fi
  elif ! command -v "${CITYPULSE_PYTHON}" >/dev/null 2>&1; then
    echo "[ERROR] Python executable is not available: ${CITYPULSE_PYTHON}" >&2
    return 1
  fi
}

copilot_require_qwen_python() {
  if [[ "${CITYPULSE_QWEN_PYTHON}" == */* ]]; then
    if [[ ! -x "${CITYPULSE_QWEN_PYTHON}" ]]; then
      echo "[ERROR] Qwen Python executable is not available: ${CITYPULSE_QWEN_PYTHON}" >&2
      return 1
    fi
  elif ! command -v "${CITYPULSE_QWEN_PYTHON}" >/dev/null 2>&1; then
    echo "[ERROR] Qwen Python executable is not available: ${CITYPULSE_QWEN_PYTHON}" >&2
    return 1
  fi
}

copilot_pid_command() {
  local pid="$1"
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    tr '\0' ' ' < "/proc/${pid}/cmdline"
  else
    ps -p "${pid}" -o args= 2>/dev/null || true
  fi
}

copilot_pid_matches() {
  local label="$1"
  local pid="$2"
  if [[ ! "${pid}" =~ ^[0-9]+$ ]] || ! kill -0 "${pid}" >/dev/null 2>&1; then
    return 1
  fi
  local command_line
  command_line="$(copilot_pid_command "${pid}")"
  case "${label}" in
    Qwen)
      [[ "${command_line}" == *"qwen_transformers_server.py"* ]]
      ;;
    Backend)
      [[ "${command_line}" == *"backend.app.main:app"* ]]
      ;;
    *)
      return 1
      ;;
  esac
}

copilot_managed_pid() {
  local label="$1"
  local pid_file="$2"
  [[ -f "${pid_file}" ]] || return 1
  local pid
  pid="$(tr -d '[:space:]' < "${pid_file}")"
  if copilot_pid_matches "${label}" "${pid}"; then
    printf '%s\n' "${pid}"
    return 0
  fi
  return 1
}

copilot_qwen_models_url() {
  printf '%s/models\n' "${CITYPULSE_QWEN_BASE_URL%/}"
}

copilot_qwen_health_url() {
  local base_url="${CITYPULSE_QWEN_BASE_URL%/}"
  if [[ "${base_url}" == */v1 ]]; then
    base_url="${base_url%/v1}"
  fi
  printf '%s/health\n' "${base_url}"
}

copilot_backend_health_url() {
  printf '%s/api/v1/health\n' "${CITYPULSE_BACKEND_URL%/}"
}

copilot_probe() {
  local url="$1"
  curl --silent --show-error --fail \
    --max-time "${CITYPULSE_HEALTH_TIMEOUT_SECONDS}" \
    "${url}" >/dev/null
}

copilot_wait_for_url() {
  local url="$1"
  local timeout_seconds="$2"
  local pid_file="${3:-}"
  local elapsed=0

  while (( elapsed < timeout_seconds )); do
    if copilot_probe "${url}"; then
      return 0
    fi
    if [[ -n "${pid_file}" && -f "${pid_file}" ]]; then
      local pid
      pid="$(tr -d '[:space:]' < "${pid_file}")"
      if [[ "${pid}" =~ ^[0-9]+$ ]] && ! kill -0 "${pid}" >/dev/null 2>&1; then
        return 2
      fi
    fi
    sleep 1
    ((elapsed += 1))
  done
  return 1
}

copilot_run_preflight() {
  copilot_require_python
  "${CITYPULSE_PYTHON}" "${COPILOT_SCRIPT_DIR}/preflight.py" \
    --repo-root "${CITYPULSE_REPO_ROOT}" \
    --qwen-model-path "${CITYPULSE_QWEN_MODEL_PATH}" \
    --embedding-model-path "${RAG_EMBEDDING_MODEL_PATH}" \
    --traffic-index-dir "${RAG_INDEX_DIR}" \
    --traffic-manifest "${RAG_KNOWLEDGE_MANIFEST}" \
    --traffic-chunks "${RAG_KNOWLEDGE_CHUNKS}" \
    --traffic-collection "${RAG_COLLECTION_NAME}" \
    --standards-index-dir "${RAG_STANDARDS_INDEX_DIR}" \
    --standards-manifest "${RAG_STANDARDS_MANIFEST}" \
    --standards-chunks "${RAG_STANDARDS_CHUNKS}" \
    --standards-collection "${RAG_STANDARDS_COLLECTION_NAME}" \
    --embedding-model "${RAG_EMBEDDING_MODEL}" \
    --embedding-dimension "${RAG_EMBEDDING_DIMENSION}" \
    --check-dependencies \
    --check-chroma \
    --require-cuda
}

copilot_show_log_tail() {
  local log_file="$1"
  if [[ -f "${log_file}" ]]; then
    echo "--- tail ${log_file} ---" >&2
    tail -n 80 "${log_file}" >&2 || true
  fi
}
