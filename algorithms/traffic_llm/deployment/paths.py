"""Frozen Traffic-Qwen V2 paths. Do not point these at holdout 44001 for calibration."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

BASE_MODEL = Path("/home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct")
HF_MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
ADAPTER_DIR = REPO_ROOT / "outputs/traffic_llm_dataset/formal_v2/training/qlora_formal_v2/adapter"
TRAINING_DIR = REPO_ROOT / "outputs/traffic_llm_dataset/formal_v2/training/qlora_formal_v2"
TRAINING_CONFIG = REPO_ROOT / "algorithms/traffic_llm/training/configs/qlora_formal_v2.yaml"
HOLDOUT_DIR = REPO_ROOT / "outputs/traffic_llm_dataset/holdout_v2_44001"
HOLDOUT_REPORT_JSON = HOLDOUT_DIR / "reports/closed_loop_eval.json"
HOLDOUT_REPORT_MD = HOLDOUT_DIR / "reports/closed_loop_eval.md"
SFT_DIR = REPO_ROOT / "outputs/traffic_llm_dataset/formal_v2"
PROMPT_COMPLETION_DIR = SFT_DIR / "prompt_completion"
DEPLOY_ROOT = REPO_ROOT / "outputs/traffic_llm_deployment"
MERGED_DIR = DEPLOY_ROOT / "traffic_qwen_v2_merged"
AWQ_DIR = DEPLOY_ROOT / "traffic_qwen_v2_awq"
MANIFEST_PATH = Path(__file__).resolve().parent / "model_manifest.json"
DEPLOY_REPORTS = DEPLOY_ROOT / "reports"

SERVED_MODEL_NAME = "traffic-qwen-v2"
DEFAULT_PORT = 8001
DEFAULT_MAX_MODEL_LEN = 8192
DEFAULT_MAX_NEW_TOKENS = 512
TRANSFORMERS_P50_MS = 3756.1010849894956
TRANSFORMERS_P95_MS = 4579.389517012169
SLOT_DROP_HALT = 0.05
