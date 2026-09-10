"""AWQ W4A16 quantization of the merged Traffic-Qwen V2 checkpoint.

Calibration text comes ONLY from formal_v2 train/val prompt_completion.
Never use seed=44001 holdout.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from algorithms.traffic_llm.dataset.io_utils import dump_json, load_json, read_jsonl
from algorithms.traffic_llm.deployment.paths import (
    AWQ_DIR,
    MANIFEST_PATH,
    MERGED_DIR,
    PROMPT_COMPLETION_DIR,
)
from algorithms.traffic_llm.deployment.write_manifest import git_commit


def _calibration_texts(*, n_samples: int = 128) -> list[str]:
    rows: list[dict[str, Any]] = []
    for split in ("train", "val"):
        path = PROMPT_COMPLETION_DIR / f"{split}.jsonl"
        if not path.is_file():
            continue
        rows.extend(read_jsonl(path))
    texts: list[str] = []
    for sample in rows:
        prompt = list(sample.get("prompt") or ())
        user = next((item.get("content") or "" for item in prompt if item.get("role") == "user"), "")
        if user:
            texts.append(str(user)[:4096])
        if len(texts) >= n_samples:
            break
    if len(texts) < 16:
        raise SystemExit("need at least 16 train/val calibration prompts; 44001 is forbidden")
    return texts[:n_samples]


def _dir_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def quantize_awq(
    *,
    model_dir: Path = MERGED_DIR,
    output_dir: Path = AWQ_DIR,
    n_calib: int = 128,
) -> dict[str, Any]:
    from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs

    ensure_cuda_runtime_libs()
    from awq import AutoAWQForCausalLM
    from transformers import AutoTokenizer

    model_dir = Path(model_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    calib = _calibration_texts(n_samples=n_calib)
    quant_config = {
        "zero_point": True,
        "q_group_size": 128,
        "w_bit": 4,
        "version": "GEMM",
    }
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    fp_bytes = _dir_bytes(model_dir)
    started = time.perf_counter()
    model = AutoAWQForCausalLM.from_pretrained(
        str(model_dir),
        safetensors=True,
        device_map="cuda:0",
        low_cpu_mem_usage=True,
    )
    model.quantize(
        tokenizer,
        quant_config=quant_config,
        calib_data=calib,
    )
    model.save_quantized(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    elapsed_s = time.perf_counter() - started
    provenance = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "merged_dir": str(model_dir),
        "output_dir": str(output_dir),
        "scheme": "AWQ W4A16",
        "quant_config": quant_config,
        "n_calib": len(calib),
        "calib_source": "outputs/traffic_llm_dataset/formal_v2/prompt_completion/{train,val}.jsonl",
        "holdout_44001_used": False,
        "elapsed_s": elapsed_s,
        "fp_bytes": fp_bytes,
        "awq_bytes": _dir_bytes(output_dir),
        "adapter_sha256": (
            load_json(model_dir / "model_provenance.json").get("adapter_sha256")
            if (model_dir / "model_provenance.json").is_file()
            else None
        ),
    }
    dump_json(output_dir / "quant_provenance.json", provenance)
    if MANIFEST_PATH.is_file():
        manifest = load_json(MANIFEST_PATH)
        artifacts = dict(manifest.get("artifacts") or {})
        artifacts["awq_dir"] = str(output_dir)
        artifacts["awq_provenance"] = str(output_dir / "quant_provenance.json")
        manifest["artifacts"] = artifacts
        dump_json(MANIFEST_PATH, manifest)
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.deployment.quantize_awq")
    parser.add_argument("--model", default=str(MERGED_DIR))
    parser.add_argument("--output", default=str(AWQ_DIR))
    parser.add_argument("--n-calib", type=int, default=128)
    args = parser.parse_args(argv)
    result = quantize_awq(
        model_dir=Path(args.model),
        output_dir=Path(args.output),
        n_calib=int(args.n_calib),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
