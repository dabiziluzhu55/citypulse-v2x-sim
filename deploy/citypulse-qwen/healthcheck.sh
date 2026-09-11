#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${CITYPULSE_QWEN_BASE_URL:-http://127.0.0.1:8001/v1}"
curl -fsS "${BASE_URL}/models" | grep -q traffic-qwen
