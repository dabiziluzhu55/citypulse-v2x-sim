"""Write / refresh the frozen Traffic-Qwen V2 model manifest."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from algorithms.traffic_llm.dataset.io_utils import dump_json, load_json, load_yaml
from algorithms.traffic_llm.deployment.paths import (
    ADAPTER_DIR,
    BASE_MODEL,
    HF_MODEL_ID,
    HOLDOUT_REPORT_JSON,
    HOLDOUT_REPORT_MD,
    MANIFEST_PATH,
    TRAINING_CONFIG,
    TRAINING_DIR,
    TRANSFORMERS_P50_MS,
    TRANSFORMERS_P95_MS,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def build_manifest() -> dict[str, Any]:
    adapter_weight = ADAPTER_DIR / "adapter_model.safetensors"
    training_report = {}
    selection = {}
    holdout = {}
    if (TRAINING_DIR / "training_report.json").is_file():
        training_report = load_json(TRAINING_DIR / "training_report.json")
    if (TRAINING_DIR / "best_checkpoint_selection.json").is_file():
        selection = load_json(TRAINING_DIR / "best_checkpoint_selection.json")
    if HOLDOUT_REPORT_JSON.is_file():
        raw = load_json(HOLDOUT_REPORT_JSON)
        decision = dict(raw.get("decision") or {})
        holdout = {
            "report_json": str(HOLDOUT_REPORT_JSON),
            "report_md": str(HOLDOUT_REPORT_MD),
            "n_llm_completed": raw.get("n_llm_completed"),
            "n_compared": raw.get("n_compared"),
            "n_invalid_event": raw.get("n_invalid_event"),
            "json_ok_rate": raw.get("json_ok_rate"),
            "schema_ok_rate": raw.get("schema_ok_rate"),
            "phase_ok_rate": raw.get("phase_ok_rate"),
            "region_ok_rate": raw.get("region_ok_rate"),
            "vs_fixed": ((raw.get("overall") or {}).get("fixed") or {}).get("win_tie_loss"),
            "mean_improvement_pct": ((raw.get("overall") or {}).get("fixed") or {}).get(
                "mean_improvement_pct"
            ),
            "transformers_inference_latency_ms": raw.get("inference_latency_ms"),
            "deployment_ready": decision.get("deployment_ready"),
        }
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "frozen",
        "served_model_name": "traffic-qwen-v2",
        "note": "Do not train or modify this adapter. Downstream merge/AWQ must cite adapter_sha256.",
        "git_commit": git_commit(),
        "training_git_commit": training_report.get("git_commit"),
        "base_model": str(BASE_MODEL),
        "hf_model_id": HF_MODEL_ID,
        "adapter_dir": str(ADAPTER_DIR),
        "adapter_sha256": file_sha256(adapter_weight) if adapter_weight.is_file() else None,
        "adapter_bytes": adapter_weight.stat().st_size if adapter_weight.is_file() else None,
        "best_checkpoint": selection.get("checkpoint"),
        "best_checkpoint_step": selection.get("step"),
        "best_checkpoint_epoch": selection.get("epoch"),
        "val_teacher_phase_slot_agreement": selection.get("teacher_phase_slot_agreement"),
        "val_whole_plan_exact_match": selection.get("whole_plan_exact_match"),
        "training_config_path": str(TRAINING_CONFIG),
        "training_config": load_yaml(TRAINING_CONFIG),
        "holdout_v2_44001": holdout,
        "transformers_offline_p50_ms": TRANSFORMERS_P50_MS,
        "transformers_offline_p95_ms": TRANSFORMERS_P95_MS,
        "artifacts": {
            "merged_dir": None,
            "awq_dir": None,
        },
    }


def _merge_existing_artifacts(manifest: dict[str, Any]) -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return manifest
    previous = load_json(MANIFEST_PATH)
    artifacts = dict(previous.get("artifacts") or {})
    artifacts.update({k: v for k, v in (manifest.get("artifacts") or {}).items() if v})
    manifest["artifacts"] = artifacts
    return manifest


def main() -> int:
    manifest = _merge_existing_artifacts(build_manifest())
    dump_json(MANIFEST_PATH, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
