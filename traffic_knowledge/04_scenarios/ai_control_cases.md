# CityPulse-Qwen 典型管控案例

以下案例存储 **控制原则和决策依据**，不写死具体 `official_phase_no`，除非实现时能对照该路口官方方案确认。**【规划功能】** 所述 AI scope、决策周期和 takeover 尚未实现。**【项目事实】** 事件类型与可执行动作以代码为准。

## 案例 1：交通事故导致局部车道容量下降

### 事件

用户注入 `accident`：指定路口某进口车道、`position_ratio`、`start_seconds`–`end_seconds`。SUMO 在该位置停放障碍车，该车道有效容量下降。

### 可能观察到的状态

事件车道 `current_allowed_speed_mps` 或实际速度显著低于同向其他车道；`halting_count`、`waiting_time`、`occupancy` 持续上升；同向相邻车道仍可能流动。上游进口随后排队增长。规则检测可能给出 `localized_blockage`，原因候选可为 `stopped_or_crashed_vehicle`，这不是外部确认事故。

### 预测

若 NarrowNet-TDP 或 moving_average 显示上游路口未来 60 秒 `vehicle_count` 继续上升，应提高回溢预警，而不是只看当前帧。

### 推荐 AI scope

事件路口 + 向该进口送车的直接上游 + 接收该方向放行的直接下游。`xiongan_20` 不要扩到全网。

### 管控目标与原则

1. 减少继续向受阻车道/下游送入的相位保持时间。
2. 保护交叉口内部清空，避免回溢封住横向 movement。
3. 关注可替代转向是否被事件车道堵住。
4. 不得输出清障或改路网动作。

### 恢复

事故事件结束后障碍车移除，但上游排队可能仍在。恢复期继续观察停止数和速度；连续多个决策周期回到基线后再结束 takeover，把相位决策交还 baseline controller。

## 案例 2：施工占道

### 事件

用户注入 `lane_closure`：指定车道在时窗内对一类机动车 disallowed。这是持续容量下降，不是短脉冲需求。

### 状态与传播

放绿后该车道仍无法服务原方向需求；车辆可能挤向同向剩余车道，换道增加急刹风险。上游先出现排队，随后可能 `spillback` 到更上游。下游若不再获得该方向到达，占用可能下降，不能据此判断全网好转。

### 管控目标与原则

1. 上游限流：不要用更长绿灯把车推入已关闭车道所在路段。
2. 下游协调：为仍开放的转向保留清空，避免剩余车道二次阻塞。
3. 重点指标：事件车道占用/停止、同向剩余车道速度差、上游 `halting_count`、下游占用、完成率。
4. 施工时段通常长于事故，AI window 应覆盖整个 closure，并加恢复段。

### 恢复

lane_closure 结束后车道重新允许通行。若立即按高峰最大放行，可能造成启动浪涌。恢复期逐步解除上游限流。

## 案例 3：临时道路限速

### 事件

用户注入 `speed_limit`：指定车道在时窗内降低允许速度。DisturbanceTarget 默认目标速度 5 m/s，且必须低于原 `max_speed`。这是 **到达节奏与通行能力下降**，不是车道封闭。

### 为什么不能等价成 lane_closure

封道使该车道一类机动车无法使用；限速后车辆仍可通过，但运行速度下降，同一绿灯通过的车辆可能减少，车队行程时间变长，上游到达更密或更不均匀。相邻信号若仍按原行程时间协调，可能错过车队或把车推入正在减速的路段。

### 可能观察到的状态

事件车道 `current_allowed_speed_mps` 下降；`mean_speed` 低于邻道；`vehicle_count` / `occupancy` 可能上升，但不一定出现封道那样的持续零放行。上游 `halting_count` 可能变化。不得因为车道仍“绿且可走”就判断无影响。

### 管控目标与原则

1. 按新的较慢到达重新理解上下游相位优先级，而不是关闭该方向。
2. 上游不要用更长绿灯把高速车队送入已限速路段造成急刹堆积。
3. 下游按延迟到达调整接收，避免空放或过早切换。
4. 重点指标：事件车道允许速度与实际速度、上游排队、急刹、完成率。

### 恢复

限速解除后允许速度恢复。恢复期观察速度是否回升、急刹是否下降，再结束 takeover。

## 案例 4：大型活动开场（ingress）

### 事件

用户注入 `major_event_opening`：在时窗内从来源车道向场馆车道按 `vehicle_count` 生成到达流。这是 **入场需求集中**，不是散场，也不是容量损失。

### 与散场的区别

开场车辆向目标区域集中，瓶颈通常在入口走廊和场馆进口。若把开场按散场来管——加长离场方向、压缩入场——会把到达车队堵在外围，并与入场需求冲突。

### 推荐 AI scope

场馆所在路口 + 主要入场方向的上游走廊路口。保护入口存储，限制与入场冲突的反向过饱和放行。不要平均照顾所有进口。

### 管控目标与原则

1. 识别 ingress 主方向，提高入口走廊的接收一致性。
2. 上游 gating：避免过早把外围所有进口都放到最大，导致入口交叉口内部堵死。
3. 结合预测：若入口路口未来 60 秒车辆数已高，外围不宜继续加码送入。
4. 生成结束后仍可能有在途到达，需要短暂恢复控制。

## 案例 5：大型活动散场（egress）

### 事件

用户注入 `major_event_closing`：在时窗内从场馆车道向目的车道生成 `vehicle_count` 辆离开车辆。这是需求激增，不是容量损失，也不是开场。

### 为什么不能只控制出口单路口

散场车辆离开第一个路口后仍占用下游路段。若只把出口相位加长，而下游路口仍按原配时或反向高峰服务，疏散走廊会在第二、第三个路口形成新的瓶颈，并回灌出口。入口走廊若同时保持高绿时，还会与离场流冲突。

### 推荐 AI scope

场馆所在路口 + 主要离场方向一跳或两跳走廊路口。不要平均照顾所有进口。开场策略见案例 4，二者不得共用同一套相位优先级。

### 管控目标与原则

1. 识别疏散主方向，提高该走廊的接收与放行一致性。
2. 限制与离场流冲突的反向或转入相位过度保持。
3. 结合预测：若下游路口未来 60 秒车辆数已高，出口不宜继续加码。
4. 活动车辆生成结束后，走廊排队仍可能存在，需要恢复控制再退出。

## 案例通用检查清单

回答“当前系统允许 AI 控制哪些路口”时：必须属于当前 preset，且在规划 scope 内。回答“方案如何变成 SUMO 动作”时：高层 AI plan → schema/scope/phase/safety → Executor → `{intersection_id: {target_phase}}` → `SafePhaseController`。写不出合法相位时，整单保持 baseline。

五种可注入事件均需单独策略：`accident`、`lane_closure`、`speed_limit`、`major_event_opening`、`major_event_closing`。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; simulation/sumo/engine/events.py; algorithms/event_detection/semantics.py
   - 用于支持：五类注入事件的真实效果与检测语义。
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
2. FHWA Reducing Non-Recurring Congestion
   - 发布机构：美国联邦公路管理局
   - URL：https://ops.fhwa.dot.gov/program_areas/reduce-non-cong.htm
   - 用于支持：事故、施工和活动造成的非经常性拥堵机理。
