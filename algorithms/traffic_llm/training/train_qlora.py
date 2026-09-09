"""QLoRA smoke trainer for frozen Signal SFT. Not used by Backend."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.training.train_qlora")
    parser.add_argument("--config", default="algorithms/traffic_llm/training/configs/qlora_smoke.yaml")
    args = parser.parse_args(argv)
    cfg = _load_yaml(Path(args.config))
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
    from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs

    ensure_cuda_runtime_libs()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback
    from trl import SFTConfig, SFTTrainer

    dataset_dir = Path(cfg["dataset_dir"])
    output_dir = Path(cfg["output_dir"])
    adapter_dir = output_dir / "adapter"
    output_dir.mkdir(parents=True, exist_ok=True)
    train_rows = _read_jsonl(dataset_dir / "sft" / "train.jsonl")
    val_rows = _read_jsonl(dataset_dir / "sft" / "val.jsonl")
    if not train_rows:
        raise SystemExit(f"no train samples in {dataset_dir / 'sft' / 'train.jsonl'}")

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"], trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    def to_text(sample: dict[str, Any]) -> str:
        return tokenizer.apply_chat_template(
            sample["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )

    train_ds = Dataset.from_list([{"text": to_text(row)} for row in train_rows])
    eval_ds = Dataset.from_list([{"text": to_text(row)} for row in val_rows]) if val_rows else None

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
    model = prepare_model_for_kbit_training(model)
    model.enable_input_require_grads()
    lora_cfg = dict(cfg.get("lora") or {})
    peft_config = LoraConfig(
        r=int(lora_cfg.get("r", 16)),
        lora_alpha=int(lora_cfg.get("lora_alpha", 32)),
        lora_dropout=float(lora_cfg.get("lora_dropout", 0.05)),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(lora_cfg.get("target_modules") or ("q_proj", "v_proj")),
    )
    model = get_peft_model(model, peft_config)
    model.config.use_cache = False
    if cfg.get("gradient_checkpointing", True):
        model.gradient_checkpointing_enable()

    loss_history: list[dict[str, Any]] = []

    class LossCallback(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                loss_history.append({"step": state.global_step, **dict(logs)})

    sft_args = SFTConfig(
        output_dir=str(output_dir / "trainer"),
        num_train_epochs=float(cfg.get("num_train_epochs", 1)),
        max_steps=int(cfg.get("max_steps", -1)),
        per_device_train_batch_size=int(cfg.get("per_device_train_batch_size", 1)),
        gradient_accumulation_steps=int(cfg.get("gradient_accumulation_steps", 8)),
        learning_rate=float(cfg.get("learning_rate", 2e-4)),
        lr_scheduler_type=str(cfg.get("lr_scheduler_type", "cosine")),
        warmup_ratio=float(cfg.get("warmup_ratio", 0.03)),
        logging_steps=int(cfg.get("logging_steps", 1)),
        save_steps=int(cfg.get("save_steps", 30)),
        bf16=bool(cfg.get("bf16", True)),
        gradient_checkpointing=bool(cfg.get("gradient_checkpointing", True)),
        seed=int(cfg.get("seed", 0)),
        max_length=int(cfg.get("max_seq_length", 3072)),
        dataset_text_field="text",
        packing=False,
        report_to="none",
        save_strategy="steps",
        eval_strategy="no",
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
        callbacks=[LossCallback()],
    )
    train_result = trainer.train()
    adapter_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    metrics = dict(train_result.metrics)
    payload = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(),
        "config": cfg,
        "n_train": len(train_rows),
        "n_val": len(val_rows),
        "metrics": metrics,
        "loss_history": loss_history,
        "note": "QLoRA smoke only. Do not deploy this adapter to Backend.",
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"adapter": str(adapter_dir), "metrics": metrics, "git_commit": payload["git_commit"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
