# Traffic-Qwen training (offline)

本目录只做 **Qwen2.5-7B-Instruct QLoRA**，不进入 Backend / vLLM / 生产接管。

默认基座（本地已有权重）：

```text
/home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct
```

对应 HuggingFace id：`Qwen/Qwen2.5-7B-Instruct`。

当前 smoke 使用 PEFT + TRL + bitsandbytes 4-bit NF4，不 merge、不 AWQ、不替换线上服务。

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
