"""Launch a frozen Traffic-Qwen vLLM OpenAI-compatible server."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs
from algorithms.traffic_llm.deployment.paths import (
    ADAPTER_DIR,
    AWQ_DIR,
    BASE_MODEL,
    DEFAULT_MAX_MODEL_LEN,
    DEFAULT_PORT,
    MERGED_DIR,
    SERVED_MODEL_NAME,
)


def build_command(
    *,
    mode: str,
    port: int = DEFAULT_PORT,
    gpu_memory_utilization: float = 0.85,
    max_model_len: int = DEFAULT_MAX_MODEL_LEN,
    served_name: str = SERVED_MODEL_NAME,
) -> list[str]:
    vllm = shutil.which("vllm") or sys.executable
    prefix = [vllm, "serve"] if Path(vllm).name == "vllm" else [sys.executable, "-m", "vllm.entrypoints.openai.api_server"]
    common = [
        "--host",
        "0.0.0.0",
        "--port",
        str(int(port)),
        "--max-model-len",
        str(int(max_model_len)),
        "--gpu-memory-utilization",
        str(gpu_memory_utilization),
        "--attention-backend",
        "FLASH_ATTN",
        "--disable-log-stats",
        "--no-enable-log-requests",
    ]
    if mode == "lora":
        # Base keeps its own served name. API clients must call traffic-qwen-v2
        # (the LoRA module), otherwise they would hit the untuned base model.
        return [
            *prefix,
            str(BASE_MODEL),
            "--enable-lora",
            "--lora-modules",
            f"{served_name}={ADAPTER_DIR}",
            "--max-lora-rank",
            "16",
            "--max-loras",
            "1",
            "--dtype",
            "bfloat16",
            "--served-model-name",
            "Qwen2.5-7B-Instruct",
            *common,
        ]
    if mode == "merged":
        return [
            *prefix,
            str(MERGED_DIR),
            "--dtype",
            "bfloat16",
            "--served-model-name",
            served_name,
            *common,
        ]
    if mode == "awq":
        return [
            *prefix,
            str(AWQ_DIR),
            "--quantization",
            "awq",
            "--dtype",
            "float16",
            "--served-model-name",
            served_name,
            *common,
        ]
    raise ValueError(f"unknown mode={mode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.deployment.serve_vllm")
    parser.add_argument("--mode", choices=("lora", "merged", "awq"), required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--max-model-len", type=int, default=DEFAULT_MAX_MODEL_LEN)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)
    command = build_command(
        mode=args.mode,
        port=args.port,
        gpu_memory_utilization=args.gpu_memory_utilization,
        max_model_len=args.max_model_len,
    )
    printable = " ".join(f"'{item}'" if " " in item else item for item in command)
    print(printable, flush=True)
    if args.print_only:
        return 0
    ensure_cuda_runtime_libs()
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        cudnn = Path(conda) / "lib/python3.10/site-packages/nvidia/cudnn/lib"
        if cudnn.is_dir():
            current = os.environ.get("LD_LIBRARY_PATH", "")
            parts = [str(cudnn), *[p for p in current.split(os.pathsep) if p]]
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
