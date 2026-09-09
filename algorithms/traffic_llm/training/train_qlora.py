"""QLoRA trainer for frozen Signal SFT. Prompt-completion only. Not used by Backend."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from algorithms.traffic_llm.dataset.token_budget import validate_prompt_completion_rows


def _git_commit() -> str:
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


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"training yaml must be a mapping: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _to_prompt_completion(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("prompt") and row.get("completion"):
        return {
            "prompt": list(row["prompt"]),
            "completion": list(row["completion"]),
        }
    messages = list(row.get("messages") or ())
    return {
        "prompt": [item for item in messages if item.get("role") in {"system", "user"}],
        "completion": [item for item in messages if item.get("role") == "assistant"],
    }


def _load_split_rows(dataset_dir: Path, split: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    pc_name = str(cfg.get("prompt_completion_dirname") or "prompt_completion")
    sft_name = str(cfg.get("sft_dirname") or "sft")
    pc_path = dataset_dir / pc_name / f"{split}.jsonl"
    sft_path = dataset_dir / sft_name / f"{split}.jsonl"
    path = pc_path if pc_path.is_file() else sft_path
    return [_to_prompt_completion(row) for row in _read_jsonl(path)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.training.train_qlora")
    parser.add_argument("--config", default="algorithms/traffic_llm/training/configs/qlora_smoke_v2.yaml")
    args = parser.parse_args(argv)
    cfg = _load_yaml(Path(args.config))
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
    from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs

    ensure_cuda_runtime_libs()

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback
    from trl import SFTConfig, SFTTrainer

    dataset_dir = Path(cfg["dataset_dir"])
    output_dir = Path(cfg["output_dir"])
    adapter_dir = output_dir / "adapter"
    output_dir.mkdir(parents=True, exist_ok=True)
    train_rows = _load_split_rows(dataset_dir, "train", cfg)
    val_rows = _load_split_rows(dataset_dir, "val", cfg)
    if not train_rows:
        raise SystemExit(f"no train samples in {dataset_dir}")

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"], trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    max_length = int(cfg.get("max_seq_length", 4096))
    train_check = validate_prompt_completion_rows(
        train_rows, tokenizer, max_length=max_length, split="train"
    )
    val_check = (
        validate_prompt_completion_rows(val_rows, tokenizer, max_length=max_length, split="val")
        if val_rows
        else {"passed": True, "assistant_truncation_rate": 0.0, "n": 0, "errors": []}
    )
    token_report = {"train": train_check, "val": val_check}
    (output_dir / "token_validation.json").write_text(
        json.dumps(
            {
                "max_length": max_length,
                "train_passed": train_check["passed"],
                "val_passed": val_check["passed"],
                "train_truncation_rate": train_check["assistant_truncation_rate"],
                "val_truncation_rate": val_check.get("assistant_truncation_rate"),
                "errors": (train_check.get("errors") or [])[:50] + (val_check.get("errors") or [])[:50],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if not train_check["passed"] or not val_check["passed"]:
        raise SystemExit(
            "FAIL: assistant target missing or truncated by max_length. "
            "Do not train. Compress Observation V2 instead of raising max_length blindly. "
            f"train_truncation_rate={train_check['assistant_truncation_rate']} "
            f"val_truncation_rate={val_check.get('assistant_truncation_rate')}"
        )

    train_ds = Dataset.from_list(train_rows)
    eval_ds = Dataset.from_list(val_rows) if val_rows else None

    compute_dtype = torch.bfloat16 if cfg.get("bf16", True) else torch.float16
    quant = dict(cfg.get("quantization") or {})
    bnb = BitsAndBytesConfig(
        load_in_4bit=bool(quant.get("load_in_4bit", True)),
        bnb_4bit_quant_type=str(quant.get("bnb_4bit_quant_type", "nf4")),
        bnb_4bit_use_double_quant=bool(quant.get("bnb_4bit_use_double_quant", True)),
        bnb_4bit_compute_dtype=compute_dtype,
    )
    model = AutoModelForCausalLM.from_pretrained(
        cfg["base_model"],
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=compute_dtype,
    )
    lora_cfg = dict(cfg.get("lora") or {})
    peft_config = LoraConfig(
        r=int(lora_cfg.get("r", 16)),
        lora_alpha=int(lora_cfg.get("lora_alpha", 32)),
        lora_dropout=float(lora_cfg.get("lora_dropout", 0.05)),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(lora_cfg.get("target_modules") or ("q_proj", "v_proj")),
    )
    model.config.use_cache = False

    loss_history: list[dict[str, Any]] = []
    peak_mem = {"allocated_gb": 0.0, "reserved_gb": 0.0}

    class LossCallback(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                loss_history.append({"step": state.global_step, **dict(logs)})
            if torch.cuda.is_available():
                allocated = torch.cuda.max_memory_allocated() / (1024 ** 3)
                reserved = torch.cuda.max_memory_reserved() / (1024 ** 3)
                peak_mem["allocated_gb"] = max(peak_mem["allocated_gb"], allocated)
                peak_mem["reserved_gb"] = max(peak_mem["reserved_gb"], reserved)

    eval_strategy = str(cfg.get("eval_strategy", "no"))
    save_strategy = str(cfg.get("save_strategy", "epoch" if eval_strategy == "epoch" else "steps"))
    max_steps = int(cfg.get("max_steps", -1))
    sft_args = SFTConfig(
        output_dir=str(output_dir / "trainer"),
        num_train_epochs=float(cfg.get("num_train_epochs", 1)),
        max_steps=max_steps,
        per_device_train_batch_size=int(cfg.get("per_device_train_batch_size", 1)),
        per_device_eval_batch_size=int(cfg.get("per_device_eval_batch_size", 1)),
        gradient_accumulation_steps=int(cfg.get("gradient_accumulation_steps", 8)),
        learning_rate=float(cfg.get("learning_rate", 2e-4)),
        lr_scheduler_type=str(cfg.get("lr_scheduler_type", "cosine")),
        warmup_ratio=float(cfg.get("warmup_ratio", 0.03)),
        logging_steps=int(cfg.get("logging_steps", 1)),
        save_steps=int(cfg.get("save_steps", 30)),
        bf16=bool(cfg.get("bf16", True)),
        gradient_checkpointing=bool(cfg.get("gradient_checkpointing", True)),
        seed=int(cfg.get("seed", 0)),
        max_length=max_length,
        packing=False,
        report_to="none",
        save_strategy=save_strategy,
        eval_strategy=eval_strategy if eval_ds is not None else "no",
        load_best_model_at_end=bool(cfg.get("load_best_model_at_end", False)) and eval_ds is not None and eval_strategy != "no",
        metric_for_best_model=str(cfg.get("metric_for_best_model", "eval_loss")),
        greater_is_better=bool(cfg.get("greater_is_better", False)),
        completion_only_loss=True,
        optim="paged_adamw_8bit",
        gradient_checkpointing_kwargs={"use_reentrant": False},
        dataloader_pin_memory=False,
    )
    trainer = SFTTrainer(
        model=model,
        args=sft_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=peft_config,
        callbacks=[LossCallback()],
    )
    n_trainable = sum(int(p.requires_grad) for p in trainer.model.parameters())
    n_trainable_params = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
    if n_trainable == 0 or n_trainable_params == 0:
        raise SystemExit(
            "FAIL: no trainable LoRA parameters. Do not train. "
            "SFTTrainer must wrap the 4-bit base model via peft_config; "
            "pre-wrapping then calling prepare_peft_model freezes adapters."
        )
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    train_result = trainer.train()
    adapter_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    best_ckpt = getattr(trainer.state, "best_model_checkpoint", None)
    if best_ckpt:
        (output_dir / "best_checkpoint.txt").write_text(str(best_ckpt) + "\n", encoding="utf-8")
    metrics = dict(train_result.metrics)
    payload = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(),
        "config": cfg,
        "n_train": len(train_rows),
        "n_val": len(val_rows),
        "n_trainable_tensors": n_trainable,
        "n_trainable_params": n_trainable_params,
        "max_length": max_length,
        "completion_only_loss": True,
        "metrics": metrics,
        "loss_history": loss_history,
        "best_checkpoint": best_ckpt,
        "gpu_memory_peak": peak_mem,
        "token_validation": {
            "train_truncation_rate": train_check["assistant_truncation_rate"],
            "val_truncation_rate": val_check.get("assistant_truncation_rate"),
        },
        "note": "QLoRA adapter only. Do not merge/AWQ/vLLM/Backend until holdout test passes.",
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "adapter": str(adapter_dir),
                "metrics": metrics,
                "best_checkpoint": best_ckpt,
                "gpu_memory_peak": peak_mem,
                "git_commit": payload["git_commit"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
