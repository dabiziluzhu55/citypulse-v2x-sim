# Traffic-Qwen V2 部署转换（离线）

本目录只做 **冻结 V2 LoRA → vLLM → merge → AWQ**。不训练、不改 adapter、不改 Backend / Frontend / RAG / Docker。

服务器环境（本机实测）：

```text
conda env: v2x-ai-py310
python: /home/kemove/anaconda3/envs/v2x-ai-py310/bin/python
vllm: /home/kemove/anaconda3/envs/v2x-ai-py310/bin/vllm   # 0.29.0
torch: 2.13.0+cu130
GPU: 2× RTX 4090；本轮使用 CUDA_VISIBLE_DEVICES=1（GPU0 常被占用）
SUMO_HOME=/usr/share/sumo
基座: /home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct
adapter: outputs/traffic_llm_dataset/formal_v2/training/qlora_formal_v2/adapter
adapter sha256: 2e99cb94b54dd86c0342f244802dc5744cb67bf85c3ff2e8aa99560fddc86fe5
served name: traffic-qwen-v2
port: 8001
```

解码固定：`temperature=0`（greedy）。vLLM 的 JSON schema / `guided_json` 只约束生成；**Backend 以后仍必须走 `AIControlPlan.from_mapping` 合法性校验**。

校准数据只允许 `outputs/traffic_llm_dataset/formal_v2/prompt_completion/{train,val}.jsonl`。禁止 44001 holdout。

停服时 `pkill -f 'vllm serve'` 经常留着 `VLLM::EngineCore`。用 `nvidia-smi` 找到 GPU1 上的 EngineCore PID 再 `kill -9`。

在仓库根目录执行下面所有命令。

```bash
cd /home/kemove/devdata1/zrl/citypulse-v2x-sim
export PYTHONPATH=.
export SUMO_HOME=/usr/share/sumo
export CONDA_PREFIX=/home/kemove/anaconda3/envs/v2x-ai-py310
export PATH="${CONDA_PREFIX}/bin:/usr/share/sumo/bin:${PATH}"
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib/python3.10/site-packages/nvidia/cudnn/lib:${CONDA_PREFIX}/lib/python3.10/site-packages/nvidia/cu13/lib:${LD_LIBRARY_PATH:-}"
export CUDA_VISIBLE_DEVICES=1
PY=/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python
```

`cudnn/lib` 必须放在 `LD_LIBRARY_PATH` 最前面（目录里同时有 `libcudnn.so.8` 和 `.9`，flashinfer 需要先命中 `.9`）。

若尚未安装推理依赖（不要装进别的 env）：

```bash
"${PY}" -m pip install 'vllm==0.29.0' 'openai>=1.40' 'autoawq'
```

## 0. 冻结 manifest

```bash
"${PY}" -m algorithms.traffic_llm.deployment.write_manifest
```

产物：`algorithms/traffic_llm/deployment/model_manifest.json`

## 1. LoRA-vLLM 启动

```bash
"${PY}" -m algorithms.traffic_llm.deployment.serve_vllm --mode lora --port 8001
```

本机实测等价命令：

```bash
/home/kemove/anaconda3/envs/v2x-ai-py310/bin/vllm serve \
  /home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct \
  --enable-lora \
  --lora-modules traffic-qwen-v2=/home/kemove/devdata1/zrl/citypulse-v2x-sim/outputs/traffic_llm_dataset/formal_v2/training/qlora_formal_v2/adapter \
  --max-lora-rank 16 \
  --max-loras 1 \
  --dtype bfloat16 \
  --served-model-name Qwen2.5-7B-Instruct \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --attention-backend FLASH_ATTN \
  --disable-log-stats \
  --no-enable-log-requests
```

客户端必须请求 `traffic-qwen-v2`（LoRA 模块名）。`--served-model-name` 是基座名，打到 `Qwen2.5-7B-Instruct` 会绕过 adapter。

健康检查：

```bash
curl -s http://127.0.0.1:8001/v1/models
curl -s http://127.0.0.1:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"traffic-qwen-v2","temperature":0,"max_tokens":32,"messages":[{"role":"user","content":"ping"}]}'
```

## 2. Merge

先停掉占用 GPU1 的 vLLM，再 merge。

```bash
"${PY}" -m algorithms.traffic_llm.deployment.merge_adapter \
  --base /home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct \
  --adapter outputs/traffic_llm_dataset/formal_v2/training/qlora_formal_v2/adapter \
  --output outputs/traffic_llm_deployment/traffic_qwen_v2_merged
```

Merge 后用 merged-vLLM 做结构化回归，通过后再 AWQ：

```bash
"${PY}" -m algorithms.traffic_llm.deployment.serve_vllm --mode merged --port 8001
```

```bash
"${PY}" -m algorithms.traffic_llm.deployment.regression_eval \
  --base-url http://127.0.0.1:8001 \
  --model traffic-qwen-v2 \
  --tag merged \
  --baseline outputs/traffic_llm_deployment/reports/regression_lora.json
```

## 3. AWQ

必须在 merge 结构化回归通过之后。校准只用 train/val，128 条。

```bash
"${PY}" -m algorithms.traffic_llm.deployment.quantize_awq \
  --model outputs/traffic_llm_deployment/traffic_qwen_v2_merged \
  --output outputs/traffic_llm_deployment/traffic_qwen_v2_awq \
  --n-calib 128
```

## 4. AWQ-vLLM 启动

Copilot 需要 OpenAI tools，因此 AWQ 服务必须打开 auto tool choice。
AI Control 仍走 Observation V2 + `guided_json`，不依赖 tools。

```bash
"${PY}" -m algorithms.traffic_llm.deployment.serve_vllm --mode awq --port 8001
```

本机实测等价命令：

```bash
/home/kemove/anaconda3/envs/v2x-ai-py310/bin/vllm serve \
  /home/kemove/devdata1/zrl/citypulse-v2x-sim/outputs/traffic_llm_deployment/traffic_qwen_v2_awq \
  --quantization awq \
  --dtype float16 \
  --served-model-name traffic-qwen-v2 \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --attention-backend FLASH_ATTN \
  --disable-log-stats \
  --no-enable-log-requests \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

## 5. Benchmark

对当前已启动的服务：

```bash
"${PY}" -m algorithms.traffic_llm.deployment.benchmark_server \
  --base-url http://127.0.0.1:8001 \
  --model traffic-qwen-v2 \
  --n 20 \
  --warmup 2 \
  --tag lora
```

AWQ 服务起来后把 `--tag awq`。

## 6. Regression

formal_v2 val（70 条 Observation V2），不是 44001：

```bash
"${PY}" -m algorithms.traffic_llm.deployment.regression_eval \
  --base-url http://127.0.0.1:8001 \
  --model traffic-qwen-v2 \
  --tag lora

"${PY}" -m algorithms.traffic_llm.deployment.regression_eval \
  --base-url http://127.0.0.1:8001 \
  --model traffic-qwen-v2 \
  --tag awq \
  --baseline outputs/traffic_llm_deployment/reports/regression_lora.json
```

slot agreement 绝对下降超过 5 个百分点会返回退出码 3，应停止部署。

## 7. 15 场景 SUMO 闭环（AWQ-vLLM）

```bash
"${PY}" -m algorithms.traffic_llm.deployment.closed_loop_vllm \
  --dataset outputs/traffic_llm_dataset/holdout_v2_44001 \
  --base-url http://127.0.0.1:8001 \
  --model traffic-qwen-v2 \
  --policy traffic_qwen_v2_awq \
  --seed 44001 \
  --resume
```

覆盖 5 event × 3 scope，并轮转 3 个 period。只验证转换没有破坏能力，不调模型。

实测报告：

```text
outputs/traffic_llm_deployment/reports/benchmark_lora.json
outputs/traffic_llm_deployment/reports/benchmark_awq.json
outputs/traffic_llm_deployment/reports/regression_lora.json
outputs/traffic_llm_deployment/reports/regression_merged.json
outputs/traffic_llm_deployment/reports/regression_awq.json
outputs/traffic_llm_deployment/reports/closed_loop_awq_15.json
```
