#!/usr/bin/env bash
# DEV ONLY — starts backend + checks Copilot wiring.
# CityPulse-Qwen inference must be provided externally (vLLM container or serve_vllm.py).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

echo "[INFO] Running Copilot artifact preflight..."
copilot_run_preflight
echo "[INFO] Ensure CityPulse-Qwen vLLM is running (default http://127.0.0.1:8001/v1)."
"${SCRIPT_DIR}/start_backend.sh"
"${SCRIPT_DIR}/check_copilot.sh"
