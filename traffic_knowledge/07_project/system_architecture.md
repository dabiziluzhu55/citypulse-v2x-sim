# citypulse-v2x-sim 系统架构

## 运行链路

前端通过 HTTP/WebSocket 访问 FastAPI Backend。Backend 不直接持有 TraCI 仿真循环，而是校验业务请求、创建会话并通过本地或 Redis/Celery 管理器调度 SUMO Worker。Worker 独占 libsumo，加载 `simulation/` 和产品部署包 `traffic_control/`，把快照和终态产物写回会话目录。

## 控制链路

前端只提交业务名 `control_mode`。Backend 查询 `traffic_control.registry`，把 Fixed 映射到内核 `fixed`，把其余模式映射到 `algorithm` + 本地 `algorithm_module`。本地模块遵循 Protocol 2.0：`initialize` 接收路口元数据，`step` 接收观测并返回信号/车辆动作，`finish` 清理状态。信号算法输出 `target_phase`，实际黄灯和清空由仿真内核处理。

## 评估链路

`traffic_eval` 是 Backend 与无 Backend CLI 共用的部署侧口径。运行中从 `SimulationSnapshot` 采集排队、到达、急刹和临时值；终态读取 TripInfo 回填行程、等待和燃油强度。`backend/app/metrics` 只是封装，不应另建一套公式。

## 智能分析链路

Backend 智能层保存每 5 秒的路口历史。预测运行时若模型包和 STGCN 实现可用，使用过去 12 帧四特征预测未来约 60 秒车辆数；否则降级最近 12 帧移动平均，并返回 `fallback` 与原因。事件检测是信号感知规则/CUSUM 候选，不等同外部确认。

## 代码归属边界

`traffic_control/` 是产品部署算法；`algorithms/` 主要是训练、实验、评估和 V2X 研究代码，不自动进入部署镜像。未来 LLM 应由 Backend 托管，只生成结构化建议；不得直接导入训练脚本、连接 libsumo 或修改会话文件。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: README.md; backend/README.md; backend/app/services/simulation_service.py; simulation/sumo/session.py; traffic_control/protocol.py; traffic_eval/README.md; backend/app/services/prediction_runtime.py
   - 用于支持：架构、控制、评估、预测和部署归属。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim

