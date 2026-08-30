# 项目场景定义

## 场景预设

**【项目事实】** `backend/app/scenario/presets.py`：

| preset_id | Backend catalog 标签 | 受控路口 | map_template |
| --- | --- | --- | --- |
| `xiongan_20` | 雄安20路口路网 | `demo_1`–`demo_20` | `xiongan20` |
| `east_dense` | 校园周边场景 | `demo_3`、`demo_5`、`demo_6`、`demo_9` | `east_dense` |
| `west_dense` | 窄路密网片区场景 | `demo_14`、`demo_15`、`demo_19` | `west_dense` |

`demo_*` 是项目内部标识，不是公开地理路口名。RAG 以 Backend registry 为准。

校园是 Backend 业务标签，不是实测学校出入口标定。窄路密网是 Backend 业务标签；西部三路口直线距离约 195–413 m，进口路段存在约 48–291 m 的短存储，但红线宽度仍无测绘结论。

## 交通需求 scope 与管控路口

**【项目事实】** 生成清单存在独立交通 scope：`global`、`east_dense`、`west_dense`。场景编译键为 `{scenario_scope}_{period}`。Backend 启动路径当前 **不写入** `SimulationConfig.scenario_scope`，默认 `global`。因此通过 API 启动 `east_dense` / `west_dense` 时，通常是 **global 交通流 + 预设路口子集受控/观测**。不得把局部生成包的车辆数直接说成当前 API 会话需求。

## 时段与窗口

**【项目事实】** period 只能是 `morning_peak`、`off_peak`、`evening_peak`。官方时钟：07:00–09:00、14:30–16:30、17:30–19:30。启动还接收 `window_start_seconds`、`duration_seconds`、`seed`、`step_length`。前端 `flow_mode: flat` 映射 `off_peak`。比较算法必须固定这些条件。

## 扰动事件

**【项目事实】** 可注入：

| event_type | 前端标签 | SUMO 效果 |
| --- | --- | --- |
| `lane_closure` | 施工占道 | 禁止指定车道一类机动车通行 |
| `speed_limit` | 道路限速 | 降低车道允许速度；DisturbanceTarget 默认 5 m/s，且必须低于原 max_speed |
| `accident` | 交通事故 | 在 `position_ratio` 处停放障碍车 |
| `major_event_opening` | 大型活动开场 | 从来源车道向场馆车道按 `vehicle_count` 生成到达流 |
| `major_event_closing` | 大型活动散场 | 从场馆向目的车道生成离开流 |

启动用路口级 `disturbance_targets`，Backend 解析到车道；运行中用车道级 `EventRequest`。事件状态：`SCHEDULED` / `ACTIVE` / `COMPLETED` / `CANCELLED` / `FAILED`。异常停车和普通拥堵不是独立注入类型。

**【规划功能】** AI 管控范围必须是当前 preset 子集；大网优先事件路口的有限邻域。见 `ai_control_architecture.md`。

## 雄安规划与仿真事实

雄安起步区规划路网密度 10–15 公里/平方公里，雄东片区控详规约 12 公里/平方公里。这是规划口径，不是 `xiongan_20` 已测密度。20 个受控路口空间跨度约数十公里，不等于一块连续密网。

## 场景导出与 OD

**【项目事实】** `xiongan_20` 可导出全局九区 OD；`east_dense` / `west_dense` 局部包不带该全局 OD。九区对角（区内）被排除。east 四路口同属 `zone_3`、west 三路口同属 `zone_7`，故局部 OD 矩阵区际为 0。不得把全局 OD 嫁接到局部预设。

## 预测：底层粒度 vs 聚合粒度

不得写成“20 个节点 STGCN”。当前预测栈是 **NarrowNet-TDP**，`stgcn_root` 仅兼容旧配置。

**【项目事实】模型底层预测粒度**（`PredictionRuntime` / 交付包）：

- 历史 12 帧；
- 特征：车道 `vehicle_count`、`halting_count`、`mean_speed`、`occupancy`；
- 206 个训练车道节点；
- 输出未来约 60 s 的车道级 `vehicle_count`。

**【项目事实】提供给 Frontend / 规划中 LLM 的聚合预测粒度**（`IntelligenceHub` → `PredictionPayload`）：

- 按 `tls_manifest` 进口车道映射到官方路口节点后求和；
- 路口级字段：`current_vehicle_count`、`predicted_vehicle_count`、`delta`、`delta_ratio`；
- 另含 `horizon_seconds`、`model`、`fallback`、`fallback_reason`。

局部预设只采集受控路口车道，其余训练节点为 0，不能把官方 20 路口精度外推到东/西局部场景。`fallback=true` 时聚合结果来自移动平均，不是 NarrowNet-TDP。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: backend/app/scenario/presets.py; backend/app/scenario/resolver.py; backend/app/schemas/events.py; backend/app/schemas/disturbance_targets.py; backend/app/services/simulation_service.py; backend/app/services/prediction_runtime.py; backend/app/services/intelligence_runtime.py; data/maps/sumo/generated/manifests/traffic_manifest.json
   - 用于支持：预设、事件、scope 和 OD 边界。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
2. 《河北雄安新区规划纲要》
   - 发布机构：中共中央、国务院批复；中国雄安官网公开
   - 年份：2018
   - URL：https://www.xiongan.gov.cn/2018-04/21/c_129855813_8.htm
   - 用于支持：起步区路网密度规划背景。
3. 《河北雄安新区雄东片区控制性详细规划》
   - 发布机构：河北雄安新区管理委员会
   - 版本：2020 年 4 月
   - URL：https://www.xiongan.gov.cn/download/xaxqxdpqkzxxxgh.pdf
   - 用于支持：片区路网密度不可跨空间套用。
