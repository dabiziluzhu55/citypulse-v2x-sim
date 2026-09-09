"""Token accounting for prompt-completion SFT. Fail closed on truncated assistant JSON."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _ids(value: Any) -> list[int]:
    if isinstance(value, Mapping):
        value = value.get("input_ids")
    if hasattr(value, "tolist"):
        value = value.tolist()
    if value and isinstance(value[0], list):
        value = value[0]
    return list(value or ())


def tokenize_prompt_completion(
    tokenizer: Any,
    prompt: Sequence[Mapping[str, Any]],
    completion: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    prompt_ids = _ids(
        tokenizer.apply_chat_template(
            list(prompt),
            tokenize=True,
            add_generation_prompt=False,
        )
    )
    full_ids = _ids(
        tokenizer.apply_chat_template(
            list(prompt) + list(completion),
            tokenize=True,
            add_generation_prompt=False,
        )
    )
    prompt_n = len(prompt_ids)
    total_n = len(full_ids)
    completion_n = max(0, total_n - prompt_n)
    prefix_ok = full_ids[:prompt_n] == prompt_ids
    return {
        "prompt_tokens": prompt_n,
        "completion_tokens": completion_n,
        "total_tokens": total_n,
        "prefix_aligned": prefix_ok,
        "has_assistant_target": completion_n > 0,
    }


def assistant_truncated(counts: Mapping[str, Any], max_length: int) -> bool:
    if not counts.get("has_assistant_target"):
        return True
    # TRL truncate_dataset keeps the prefix, so any overflow cuts the assistant JSON.
    return int(counts.get("total_tokens") or 0) > int(max_length)


def validate_prompt_completion_rows(
    rows: Sequence[Mapping[str, Any]],
    tokenizer: Any,
    *,
    max_length: int,
    split: str = "train",
) -> dict[str, Any]:
    errors: list[str] = []
    prompt_lens: list[int] = []
    completion_lens: list[int] = []
    total_lens: list[int] = []
    truncated = 0
    missing_target = 0
    for idx, row in enumerate(rows):
        prompt = list(row.get("prompt") or ())
        completion = list(row.get("completion") or ())
        if not prompt or not completion:
            errors.append(f"{split}:{idx} missing prompt or completion")
            truncated += 1
            missing_target += 1
            continue
        counts = tokenize_prompt_completion(tokenizer, prompt, completion)
        prompt_lens.append(int(counts["prompt_tokens"]))
        completion_lens.append(int(counts["completion_tokens"]))
        total_lens.append(int(counts["total_tokens"]))
        if not counts["has_assistant_target"]:
            missing_target += 1
            truncated += 1
            errors.append(f"{split}:{idx} has no assistant target tokens")
            continue
        if assistant_truncated(counts, max_length):
            truncated += 1
            errors.append(
                f"{split}:{idx} assistant truncated: "
                f"prompt={counts['prompt_tokens']} completion={counts['completion_tokens']} "
                f"total={counts['total_tokens']} max_length={max_length}"
            )
    n = len(rows)
    return {
        "n": n,
        "n_errors": len(errors),
        "errors": errors,
        "missing_assistant_target": missing_target,
        "assistant_truncation_n": truncated,
        "assistant_truncation_rate": (truncated / n) if n else 0.0,
        "prompt_tokens": prompt_lens,
        "completion_tokens": completion_lens,
        "total_tokens": total_lens,
        "passed": n > 0 and not errors,
    }
