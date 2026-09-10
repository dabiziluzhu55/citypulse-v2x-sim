"""Merge frozen Traffic-Qwen V2 LoRA into a full Qwen2.5-7B checkpoint.

Does not import peft: this env's peft expects HybridCache, which transformers
5.17 no longer exports. Merge is W <- W + B @ A * (alpha / r).
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import torch
from safetensors import safe_open
from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig

from algorithms.traffic_llm.dataset.io_utils import dump_json, load_json
from algorithms.traffic_llm.deployment.paths import ADAPTER_DIR, BASE_MODEL, MANIFEST_PATH, MERGED_DIR
from algorithms.traffic_llm.deployment.write_manifest import file_sha256, git_commit
from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs


def _lora_delta_map(adapter: Path) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    weight = adapter / "adapter_model.safetensors"
    pairs: dict[str, dict[str, torch.Tensor]] = {}
    with safe_open(str(weight), framework="pt") as handle:
        for key in handle.keys():
            tensor = handle.get_tensor(key)
            if key.endswith(".lora_A.weight"):
                module = key[: -len(".lora_A.weight")]
                pairs.setdefault(module, {})["A"] = tensor
            elif key.endswith(".lora_B.weight"):
                module = key[: -len(".lora_B.weight")]
                pairs.setdefault(module, {})["B"] = tensor
    deltas: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
    for module, mats in pairs.items():
        if "A" not in mats or "B" not in mats:
            raise SystemExit(f"incomplete LoRA pair for {module}")
        base_key = module
        if base_key.startswith("base_model.model."):
            base_key = base_key[len("base_model.model.") :]
        deltas[base_key + ".weight"] = (mats["A"], mats["B"])
    return deltas


def merge_adapter(
    *,
    base_model: Path = BASE_MODEL,
    adapter: Path = ADAPTER_DIR,
    output_dir: Path = MERGED_DIR,
) -> dict[str, object]:
    ensure_cuda_runtime_libs()
    adapter = Path(adapter)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_json(adapter / "adapter_config.json")
    rank = int(cfg.get("r") or 16)
    alpha = float(cfg.get("lora_alpha") or 32)
    scaling = alpha / rank
    deltas = _lora_delta_map(adapter)
    tokenizer_src = adapter if (adapter / "tokenizer.json").is_file() else base_model
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_src), trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(base_model),
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    applied = 0
    with torch.no_grad():
        state = model.state_dict()
        missing = []
        for name, (lora_a, lora_b) in deltas.items():
            if name not in state:
                missing.append(name)
                continue
            delta = (lora_b.float() @ lora_a.float()) * scaling
            state[name] = (state[name].float() + delta.to(state[name].device)).to(state[name].dtype)
            applied += 1
        if missing:
            raise SystemExit(f"LoRA keys not in base model: {missing[:8]}")
        model.load_state_dict(state)
    model.save_pretrained(str(output_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(output_dir))
    gen = model.generation_config or GenerationConfig.from_model_config(model.config)
    gen.do_sample = False
    gen.temperature = None
    gen.top_p = None
    gen.top_k = None
    gen.save_pretrained(str(output_dir))
    for extra in ("chat_template.jinja", "preprocessor_config.json"):
        src = Path(tokenizer_src) / extra
        if src.is_file():
            shutil.copy2(src, output_dir / extra)
    adapter_weight = adapter / "adapter_model.safetensors"
    provenance = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "base_model": str(base_model),
        "adapter_dir": str(adapter),
        "adapter_sha256": file_sha256(adapter_weight) if adapter_weight.is_file() else None,
        "output_dir": str(output_dir),
        "torch_dtype": "bfloat16",
        "do_sample": False,
        "merge_method": "W += B @ A * (lora_alpha / r)",
        "lora_r": rank,
        "lora_alpha": alpha,
        "scaling": scaling,
        "n_lora_modules_merged": applied,
        "peft_import": "bypassed (transformers 5.17 / peft HybridCache mismatch)",
    }
    dump_json(output_dir / "model_provenance.json", provenance)
    if MANIFEST_PATH.is_file():
        manifest = load_json(MANIFEST_PATH)
        artifacts = dict(manifest.get("artifacts") or {})
        artifacts["merged_dir"] = str(output_dir)
        artifacts["merged_provenance"] = str(output_dir / "model_provenance.json")
        manifest["artifacts"] = artifacts
        dump_json(MANIFEST_PATH, manifest)
    size = sum(path.stat().st_size for path in output_dir.rglob("*") if path.is_file())
    return {**provenance, "bytes": size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.deployment.merge_adapter")
    parser.add_argument("--base", default=str(BASE_MODEL))
    parser.add_argument("--adapter", default=str(ADAPTER_DIR))
    parser.add_argument("--output", default=str(MERGED_DIR))
    args = parser.parse_args(argv)
    result = merge_adapter(
        base_model=Path(args.base),
        adapter=Path(args.adapter),
        output_dir=Path(args.output),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
