"""Watch current-version IPPO checkpoints and evaluate each stable file once."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from algorithms.ippo.controller import MODEL_VERSION


REPO_ROOT = Path(__file__).resolve().parents[2]
IPPO_DIR = Path(__file__).resolve().parent
CKPT_DIR = Path(os.getenv("IPPO_CHECKPOINT_DIR", IPPO_DIR / "checkpoints"))
EVAL_SCRIPT = IPPO_DIR / "evaluate_ckpt.py"
MANIFEST = CKPT_DIR / "evaluated.json"
SLEEP_SEC = 120


def load_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read watcher manifest {MANIFEST}: {exc}") from exc


def save_manifest(manifest: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = MANIFEST.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    temporary.replace(MANIFEST)


def is_stable(path: Path, wait_sec: float = 30.0) -> bool:
    """Wait for a checkpoint write to finish before evaluating it."""

    try:
        first_size = path.stat().st_size
        time.sleep(wait_sec)
        second_size = path.stat().st_size
    except FileNotFoundError:
        return False
    return first_size == second_size and first_size > 0


def evaluate_checkpoint(path: Path) -> dict:
    output = path.with_name(f"{path.stem}_eval.json")
    command = [sys.executable, str(EVAL_SCRIPT), str(path), "--output", str(output)]
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        completed = subprocess.run(
            command,
            check=False,
            timeout=3600,
            cwd=str(REPO_ROOT),
            text=True,
        )
        return {
            "evaluated_at": started_at,
            "status": "complete" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "output": str(output),
        }
    except subprocess.TimeoutExpired:
        return {
            "evaluated_at": started_at,
            "status": "timeout",
            "output": str(output),
        }


def scan_once(manifest: dict, *, stability_wait: float = 30.0) -> bool:
    changed = False
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoints = sorted(
        {
            *CKPT_DIR.glob(f"ippo_{MODEL_VERSION}_ep*.pt"),
            *CKPT_DIR.glob(f"ippo_{MODEL_VERSION}_parallel_ep*.pt"),
        }
    )
    for checkpoint in checkpoints:
        if checkpoint.name in manifest or not is_stable(checkpoint, stability_wait):
            continue
        print(f"[watcher] 评估: {checkpoint.name}", flush=True)
        manifest[checkpoint.name] = evaluate_checkpoint(checkpoint)
        save_manifest(manifest)
        changed = True
        print(
            f"[watcher] {manifest[checkpoint.name]['status']}: {checkpoint.name}",
            flush=True,
        )
    return changed


def main() -> None:
    print(f"[watcher] 监控 {CKPT_DIR}，每 {SLEEP_SEC}s 扫描", flush=True)
    manifest = load_manifest()
    while True:
        scan_once(manifest)
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    main()
