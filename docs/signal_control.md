# 官方信号系统与算法接口

本文说明官方信号配置、SUMO 派生产物和算法同学使用的 Python 接口。路口配时
与地图连接分层维护，算法不直接依赖 SUMO 的 `tls_id`、`linkIndex` 或灯色字符串。

## 配置分层

| 文件 | 维护内容 | 主要维护者 |
|---|---|---|
| `TotalMap_20.net.xml` | 基础路网、edge、lane、connection 和 junction | 地图/仿真组 |
| `TotalMap_20.intersections.json` | `demo_N` 到一个或多个 SUMO junction 的权威映射 | 地图/仿真组 |
| `official_tls_plans.json` | 赛方给出的周期、相位名称、绿灯、黄灯、全红和时段 | 信号配置组 |
| `official_tls_topology.json` | 官方相位对应的进口 edge、转向、保护/让行关系 | 地图/仿真组 |

两个 `official_tls` JSON 不能合并为一份的原因：

- `official_tls_plans.json` 是与地图无关的业务数据。修改绿灯秒数时不应接触
  SUMO edge 或 connection。
- `official_tls_topology.json` 是地图适配层。路网重建导致 edge 变化时，只需
  修正拓扑映射，不应重新录入官方配时。
- 构建器同时读取两份文件，并通过 `demo_N` 与
  `TotalMap_20.intersections.json` 汇合。

### 配时文件

每个路口包含一组 `programs`。一个 program 定义：

- `program_id`：运行时选择的稳定标识。
- `period_type`、`time_range`：官方时段元数据。
- `cycle_duration`：完整周期秒数。
- `phases`：官方相位编号、名称及 `green/yellow/all_red/total`。

构建器要求每个相位的 `green + yellow + all_red == total`，所有相位的
`total` 之和等于 `cycle_duration`。

### 拓扑文件

每个路口定义：

- `approaches`：有业务含义的进口名称及其 SUMO incoming edge。
- `direction_mapping`：SUMO 的 `s/l/r/t` 转向映射。
- `phases`：每个官方相位保护的进口与运动。
- `permissive`：同一相位中以小写 `g` 放行、需要让行的运动。
- `right_turn_policy`：当前使用 `permissive_always`。
- `u_turn_policy`：可选择随左转放行或 `blocked`。

大写 `G` 表示受保护绿，小写 `g` 表示必须向冲突车辆让行，`y` 表示黄灯，
`r` 表示红灯。受保护连接如果被 SUMO foe 矩阵判定冲突，构建会失败。

## generated 派生产物

`data/maps/sumo/generated/` 是可删除、可重建且不提交 Git 的目录。不要手工修改
其中的文件；每次构建会按本次选择的路口重新生成。

| 文件 | 含义 | 是否属于运行时输入 |
|---|---|---|
| `TotalMap_20.signals.net.xml` | 从基础路网派生，加入目标 TLS 并移除不兼容的空参数 | 是 |
| `official_tls.add.xml` | 由官方 program 生成的 SUMO `tlLogic` | 是 |
| `tls_manifest.json` | 官方路口、物理 TLS、相位模板、lane 和 connection 的运行时桥接数据 | 是 |
| `official_tls.sumocfg` | 组合派生路网、additional 和验证车流的 SUMO 配置 | 是 |
| `official_tls_validation.rou.xml` | 覆盖已配置正常转向的确定性验证车辆，不代表真实交通需求 | 仅验收 |
| `official_tls_connections.csv` | 供人工核对 approach、movement、linkIndex 和连接方向 | 仅诊断 |
| `official_traffic_demo_N_PERIOD.rou.xml` | 由官方 15 分钟数据生成的精确数量车流 | 是 |
| `official_traffic_demo_N_PERIOD.sumocfg` | 真实车流、信号路网和对应配时的场景入口 | 是 |
| `traffic_manifest.json` | 场景官方时间、文件名、时长和 PCU 合计 | 是 |

`tls_manifest.json` 还记录基础路网哈希、SUMO/netconvert 版本和被移除的空
`<param>` 数量。算法应读取 runner 提供的 metadata，不应自行解析 manifest。

## Python 算法接口

公共类型位于 `simulation.sumo.policy`。算法类实现：

```python
class SignalPolicy(Protocol):
    def reset(self, metadata: SimulationMetadata) -> None: ...

    def act(
        self, observation: SimulationObservation
    ) -> ControlAction | Mapping[str, int | None]: ...

    def close(self) -> None: ...
```

### 生命周期

1. runner 建立 TraCI 连接后调用一次 `reset(metadata)`。
2. runner 按 `decision_interval` 调用 `act(observation)`。
3. 算法返回 `{official_intersection_id: target_phase_no}`。
4. runner 在仿真结束或异常退出时调用 `close()`。

返回值示例：

```python
{"demo_2": 2}
```

省略某个路口或返回 `None` 表示保持当前目标。未知路口、布尔值、非整数相位或
不存在的相位会在修改信号前报错。算法不得调用
`traci.trafficlight.setRedYellowGreenState()`；TraCI 信号写权限只属于 runner。

### SimulationMetadata

| 字段 | 含义 |
|---|---|
| `intersections` | 以 `demo_N` 为键的路口元数据 |
| `decision_interval` | 算法决策间隔，单位秒 |
| `minimum_green` | runner 强制执行的最小绿灯时间 |

每个 `IntersectionMetadata` 包含：

- `intersection_id`：官方路口 ID。
- `phase_order`：允许返回的官方相位顺序。
- `phase_movements`：相位对应的主要保护运动及进口。
- `incoming_lanes`：按 approach 分组的 SUMO lane。
- `tls_ids`：底层物理 TLS，仅供诊断，不应作为算法主键。
- `junction_ids`：该官方路口对应的 SUMO junction。
- `connections`：可通行的 approach、movement、from/to edge 与 lane。

`SimulationMetadata.network_file` 给出本次派生路网路径。通常算法只需使用
`connections` 和 `incoming_lanes`，不必解析完整的 46 MB net.xml。

### SimulationObservation

每次观测包含仿真时间及各路口状态：

- `current_phase`：当前官方相位编号。
- `stage`：`GREEN`、`YELLOW` 或 `CLEARANCE`。
- `stage_elapsed`：当前阶段已经持续的秒数。
- `approaches`：各进口的 lane 观测。

每条 lane 提供 `vehicle_count`、`halting_count`、`mean_speed` 和
`waiting_time`。这些值来自 TraCI 上一个仿真步。

`observation.vehicles` 以活动车辆 ID 为键，提供 road、lane、lane index、lane
position、速度、道路限速、等待时间和完整 route，供车速引导及换道算法使用。

### 信号与车辆联合动作

旧策略返回相位字典仍然兼容。联合动作示例：

```python
from simulation.sumo.policy import ControlAction, VehicleAdvice

return ControlAction(
    signal_phases={"demo_2": 2},
    vehicle_advisories={
        "vehicle_id": VehicleAdvice(
            target_speed=8.5,
            lane_index=1,
            duration=2.0,
        )
    },
)
```

目标速度不得超过该车当前 `allowed_speed`，车辆必须仍处于仿真中，duration 必须为
正数。runner 在 duration 到期后调用 `setSpeed(vehicle_id, -1)` 恢复 SUMO 自主速度。
换道请求由 runner 调用 TraCI；车道不存在或车辆当前不能换道时，SUMO 会拒绝请求。

### 远程 HTTP 算法

算法可以独立进程或独立机器运行，只需暴露三个 JSON 接口：

- `POST /reset`：接收 `SimulationMetadata`，返回任意 JSON 对象。
- `POST /act`：接收 `SimulationObservation`，返回控制动作。
- `POST /close`：仿真退出通知，返回任意 JSON 对象。

启动命令：

```bash
pip install -r backend/requirements.txt
uvicorn algorithms.baseline.http_policy_server:app --host 127.0.0.1 --port 8001

# 另一个终端
python -m simulation.sumo.run --mode policy --gui \
  --policy-endpoint http://127.0.0.1:8001 --policy-timeout 2
```

`algorithms.baseline.http_policy_server` 是可直接联调的最长队列示例，算法同学可以复制
它的三个端点并替换 `act()` 内部决策逻辑。

`/act` 响应格式：

```json
{
  "signal_phases": {"demo_2": 2},
  "vehicle_advisories": {
    "vehicle_id": {"target_speed": 8.5, "lane_index": 1, "duration": 2.0}
  }
}
```

响应可省略任意一部分。HTTP 超时、非法 JSON、未知字段、不活动车辆、超速建议或非法
相位都会终止仿真并给出明确错误，避免算法失联后继续写入不确定控制。

### 安全约束

算法提交的是“目标相位”，不是立即写灯动作。runner 负责：

- 满足最小绿灯后才接受切换。
- 按当前 program 执行黄灯和清空阶段。
- 切换期间只保留最新目标。
- 同相位请求按无操作处理。
- 多物理 TLS 路口同步应用同一个官方相位。

## 策略实现示例

```python
from simulation.sumo.policy import SimulationMetadata, SimulationObservation


class MyPolicy:
    def reset(self, metadata: SimulationMetadata) -> None:
        self.metadata = metadata

    def act(self, observation: SimulationObservation):
        result = {}
        for intersection_id, state in observation.intersections.items():
            phase_order = self.metadata.intersections[intersection_id].phase_order
            current_index = phase_order.index(state.current_phase)
            result[intersection_id] = phase_order[(current_index + 1) % len(phase_order)]
        return result

    def close(self) -> None:
        self.metadata = None
```

策略类通过 `package.module:ClassName` 标识交给 runner。仓库中的
`algorithms.baseline.longest_queue_first.policy:LongestQueuePolicy` 是可参考的实现。

## 协作边界

- 配时同学只修改 `official_tls_plans.json`。
- 仿真同学维护 junction、approach、movement 和冲突关系。
- 算法同学只依赖 `simulation.sumo.policy` 中的稳定类型和官方路口 ID。
- 前端或后端需要状态时，应由仿真服务转发 observation，不应另开 TraCI 客户端
  与 runner 竞争信号控制权。
