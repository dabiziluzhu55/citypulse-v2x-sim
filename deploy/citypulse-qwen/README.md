# CityPulse-Qwen Production Container

独立 GPU 推理服务，运行 **CityPulse-Qwen V2 AWQ**（vLLM OpenAI-compatible API）。

## 要求

- NVIDIA GPU + CUDA 驱动
- 模型 volume 挂载到 `/models/citypulse-qwen-v2-awq`
- 不挂载 `algorithms/` 目录

## 启动

```bash
./deploy/citypulse-qwen/start.sh
```

默认监听 `8001`，served model name：`traffic-qwen-v2`。

## 健康检查

```bash
./deploy/citypulse-qwen/healthcheck.sh
```

## Backend 配置

```bash
CITYPULSE_LLM_BASE_URL=http://citypulse-qwen:8001/v1
CITYPULSE_LLM_MODEL=traffic-qwen-v2
```

训练、量化与 benchmark 脚本保留在 `algorithms/traffic_llm/` 供离线复现。
