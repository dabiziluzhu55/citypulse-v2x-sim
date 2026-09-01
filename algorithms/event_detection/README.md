# 交通事件检测路线

本目录负责 SUMO 阶段的交通事件检测。当前代码不直接做 CARLA 图像/视频识别，已实现部分主要使用结构化交通状态：车道车辆数、停车数、速度、等待时间、占有率，以及红绿灯当前状态。

## 框架

事件检测主线准备从“单标签四分类”调整为分层框架：

```text
SUMO/TraCI 结构化状态
        ↓
交通状态异常检测：normal / abnormal
状态诊断：localized_blockage / spillback / unknown_abnormal
产出是事件卡片，给前端和后端
        ↓
CARLA 或路侧视觉原因识别：事故车辆 / 施工封闭 / 无可见障碍 / unknown（暂时删除这一层）
        ↓
云端融合：事件类型、影响范围、严重程度、置信度、管控建议
```

> 旧的 `lane_blocked / spillback / speed_restriction` 四分类数据集和模型结果保留为历史 baseline 和流水线验证记录，不再作为下一轮主方案的最终事件语义。

## 收敛“事故”概念

之前把事件拆成“无异常 + 三类异常”，其中 `lane_blocked` 和 `speed_restriction` 更像局部状态，`spillback` 是拥堵传播状态，事故车辆和施工封闭则是原因。下一轮不再把它们强行放进一个互斥单标签分类器，而是拆成两层输出。

交通状态层：

| 输出 | 含义 | 主要证据 |
| --- | --- | --- |
| `normal` | 未发现异常 | 正常高峰、平峰、晚高峰都应归到这里 |
| `localized_blockage` | 局部车道或路线受阻 | 路线呈黄色、橙色或红色，按拥堵程度识别（改为拥堵程度） |
| `spillback` | 下游回溢 | 下游拥堵导致上游绿灯也释放不了 |
| `unknown_abnormal` | 未知异常 | 交通状态异常但原因或传播形态暂不确定 |

```暂时删除
原因层（carla）：

| 输出 | 含义 | 建议来源 |
| --- | --- | --- |
| `stopped_or_crashed_vehicle` | 停驶、故障或碰撞车辆占道 | CARLA/路侧视觉 |
| `construction_or_lane_closure` | 锥桶、围挡、施工封闭 | CARLA/路侧视觉 |
| `unknown` | 原因待确认 | 云端复核或人工确认 |
```

> 旧注入样本仍然保留细名，比如 `stopped_vehicle`、`lane_closure`、`collision_blockage`。在 SUMO 交通状态层，它们先统一归入 `localized_blockage`；等接入 CARLA 或视觉证据后，再细分原因。`speed_restriction` 暂时降级为历史实验和可选能力下降状态，不作为下一轮主汇报类别。

## 统一路口状态

为了方便训练模型和实时接入方便， CSV 和实时接入必须走同一套逻辑。事件检测模块只接收统一的“路口状态”。

每 `5` 秒生成一帧路口状态。路口状态可以理解成：某一秒，某个路口，每条车道现在是什么情况，红绿灯现在给不给它通行权。

### 动态帧（每 5 秒）

必需：`sequence`、`elapsed_seconds`、`intersection_id`、`lane_id`、
`vehicle_count`、`halting_count`、`mean_speed`、`waiting_time`、
`occupancy`、`lane_has_green`。

可选：`stage`、`stage_elapsed`、`queue_length_m`、
`current_allowed_speed_mps`、原始信号灯状态。

### 静态拓扑（初始化时读取）

`edge_id`、`approach_id`、`movement`、`downstream_lane_ids`。

### 会话元数据

`source`、`session_id`、`official_time`。

## 检测逻辑

规则首先按后端 `traffic_style` 的同一口径，在 `edge` 级别聚合所有车道的车辆数、停车数、车辆加权平均速度和平均占有率，再将路线分为 `free`、`slow`、`congested`、`severe`。其中 `slow`、`congested`、`severe` 分别对应前端路线的黄色、橙色和红色。

只要路线等级不是 `free`，就会进入 `localized_blockage` 候选，不再要求信号灯必须为绿灯；因此红灯排队也会按前后端一致的路线颜色识别为事件。事件仍需连续多个采样点确认，并保留短暂解除延迟，避免告警闪烁。`spillback`、`speed_restriction` 和 `accident` 等具有更强证据的状态仍按原有专门规则诊断。

确认异常后，规则诊断交通状态：

| 输出 | 判断依据 |
| --- | --- |
| `localized_blockage` | 目标路线的 `traffic_style` 为 `slow`、`congested` 或 `severe`（黄色、橙色或红色） |
| `spillback` | 上游车道具备通行权仍无法释放，且下游存在持续低速、高占有或排队证据 |
| `unknown_abnormal` | 已确认异常，但证据不足以稳定归入前两类 |

## 事件卡片

最终输出要面向后端和前端，不只是一个标签。建议事件卡片包含：

| 字段 | 含义 |
| --- | --- |
| `event_id` | 事件 ID |
| `status` | `active` 或 `ended` |
| `event_type` | `lane_blocked`、`spillback`、`speed_restriction` |
| `intersection_id` | 路口 |
| `lane_ids` | 涉及车道 |
| `start_seconds` | 开始时间 |
| `end_seconds` | 结束时间，未结束则为空 |
| `duration_seconds` | 持续时间 |
| `severity` | 严重程度，先用 `low`、`medium`、`high` |
| `confidence` | 置信度，0 到 1 |
| `evidence` | 报警证据 |
| `suggestion` | 给展示层的建议动作 |

{
  "event_id": "demo_4_-56732_1_315",
  "status": "active",
  "traffic_state": "localized_blockage",
  "suspected_cause": "unknown",
  "intersection_id": "demo_4",
  "lane_ids": ["-56732_1"],
  "start_seconds": 315,
  "end_seconds": null,
  "duration_seconds": 85,
  "severity": "medium",
  "confidence": 0.82,
  "evidence": [
    "目标车道在具备通行权时持续低速",
    "停车数持续增加",
    "相邻可比较车道仍在释放"
  ],
  "suggestion": "关注该进口车道，必要时进行人工复核或调整信号"
}

## 当前进度与后续方向

当前已完成基础链路：

SUMO 实时数据或历史 CSV
        ↓
统一成同一种路口/车道状态
        ↓
规则判断异常
        ↓
把连续异常合并为一张事件卡片

后续工作：

1. 接入 CARLA 或路侧视觉，为交通异常补充事故车辆、施工封闭等原因证据；（暂时定为不行）
2. 完善事件卡片；
3. 如需引入轻量模型，仅用于候选异常复核、误报过滤或置信度辅助，不替代规则判断。

历史四分类数据集、模型 baseline、随机事件生成和服务器 smoke 仅保留为可复现实验记录，不作为当前运行方案。
