# traffic_eval

部署侧公共交通评估

- Backend：`backend/app/metrics` 仅做封装，转发到本包
- 算法团队 / 二次开发：可直接 `import traffic_eval`，或用本机 CLI（**无需启动 FastAPI**）
- 与 `algorithms/evaluation`分离

## 无Backend本机评估

```bash
# 仓库根目录；需 SUMO_HOME + libsumo + 已生成 SUMO 产物
python -m traffic_eval \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl --seed 42 \
  --output outputs/eval_900_local.json
```

等价入口：`python -m traffic_eval.eval_cli ...`

## 经Backend HTTP评估（联调用）

需先启动 uvicorn，再跑：

```bash
python backend/tools/eval.py \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl --seed 42 \
  --output outputs/eval_900_backend.json
```

两边指标公式相同（均走 `traffic_eval`）；HTTP版多一层 API/会话编排。

## 模块

| 模块 | 作用 |
|------|------|
| `collector` | 从 `SimulationSnapshot` 采集排队/吞吐/临时油耗/急刹 |
| `tripinfo` | 终态 TripInfo 回填行程、等待、正式百公里油耗 |
| `powertrain` | 从 session/traffic manifest 读 powertrain 与燃油密度 |
| `session_hub` | 按 session 生命周期管理采集器 |
| `runner` | 无 Backend：直连 `SimulationManager` + `traffic_control` |
| `eval_cli` / `__main__` | 命令行入口（`python -m traffic_eval`） |
| `models` | `EvalResult` 与前后端字段映射 |

## 部署归属

- **Backend 容器封装**
- **SUMO Worker 容器封装**
- `traffic_control` 封装进SUMO Worker；`algorithms/` 不进部署镜像
