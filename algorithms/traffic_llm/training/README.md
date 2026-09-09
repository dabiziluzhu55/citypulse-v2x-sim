# Traffic-Qwen training (offline)

本目录只做 **Qwen2.5-7B-Instruct QLoRA**，不进入 Backend / vLLM / 生产接管。

默认基座（本地已有权重）：

```text
/home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct
```

对应 HuggingFace id：`Qwen/Qwen2.5-7B-Instruct`。

TRL `SFTTrainer` 必须接收 **4-bit 基座 + `peft_config`**。不要事先 `get_peft_model` 再交给 Trainer：TRL 会对已有 `PeftModel` 再跑 `prepare_model_for_kbit_training`，把 LoRA `B` 冻成全 0，表现为 `grad_norm=0` 且 slot agreement 相对 Base 不变。

```bash
# Observation V2 smoke（Pilot raw runs 重建，不重跑 SUMO）
python -m algorithms.traffic_llm.dataset.cli build-sft-v2 \
  --dataset outputs/traffic_llm_dataset/pilot_v2 \
  --config algorithms/traffic_llm/configs/pilot_v2.yaml

python -m algorithms.traffic_llm.dataset.cli audit \
  --dataset outputs/traffic_llm_dataset/pilot_v2 \
  --sft-dir sft_v2 \
  --prompt-completion-dir prompt_completion_v2 \
  --model /home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct \
  --max-length 4096

python -m algorithms.traffic_llm.training.train_qlora \
  --config algorithms/traffic_llm/training/configs/qlora_smoke_v2.yaml
```

正式 810 完成后使用 `qlora_formal_v1.yaml`（epoch-based，只看 val loss 选 checkpoint）。不要 merge、AWQ、vLLM。


```bash
export PYTHONPATH=.
export SUMO_HOME=/usr/share/sumo
export CUDA_VISIBLE_DEVICES=0
# PyTorch CUDA 13 runtime libs (needed by bitsandbytes on this server)
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib/python3.10/site-packages/nvidia/cu13/lib:${LD_LIBRARY_PATH:-}"
# 依赖见 requirements.txt（需与 transformers 4.57.x 匹配；bitsandbytes 0.48.0 才有 CUDA 13）

# 1) 结构化评测 Base
python -m algorithms.traffic_llm.training.eval_structured \
  --dataset outputs/traffic_llm_dataset/pilot_v2 \
  --split test \
  --model /home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct \
  --output outputs/traffic_llm_dataset/pilot_v2/training/base_eval.json

# 2) QLoRA smoke
python -m algorithms.traffic_llm.training.train_qlora \
  --config algorithms/traffic_llm/training/configs/qlora_smoke.yaml

# 3) 同样 test 集评测 adapter
python -m algorithms.traffic_llm.training.eval_structured \
  --dataset outputs/traffic_llm_dataset/pilot_v2 \
  --split test \
  --model /home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct \
  --adapter outputs/traffic_llm_dataset/pilot_v2/training/qlora_smoke/adapter \
  --output outputs/traffic_llm_dataset/pilot_v2/training/lora_eval.json
```
