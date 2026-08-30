---
information_type: project_fact
status: current
code_revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
applicable_presets:
  - xiongan_20
  - east_dense
  - west_dense
priority: high
---

# 路口拓扑目录

**【项目事实】** 本文由仓库内路网/TLS/已生成拓扑制品静态提取，代码基线 `main@1331ba87d6cd77e9052953d894a5dc83e1953009`。**【不是】** 运行时邻接 API，也 **不是** 按地图目测的结果。

运行时合法路口集合以当前会话 `scenario_preset_id` 和 Protocol 2.0 metadata 为准；本文只提供 RAG 语义理解。

## 三种邻接定义（不可混用）

| 定义 | 来源 | 判定规则 | 适用 |
| --- | --- | --- | --- |
| Protocol 2.0 `direct_neighbors` | `simulation/sumo/engine/run.py`：`outgoing_edges[A] ∩ incoming_edges[B]`；核对制品 `algorithms/ippo/regression_golden/metadata_xiongan20.json` | 路口 A 的 TLS connection `to_edge` 与路口 B 的 connection `from_edge` 有交集。即两路口共享同一条 SUMO edge 作为 A 出口/B 进口。 | 控制协议邻接；当前 20 路口几乎全空 |
| CosLight 走廊有向邻接 | `algorithms/coslight/configs/cloud_topology_v2.json`；生成参数 `vehicle_class=passenger`，`max_search_distance_m=10000`，`max_corridor_distance_m=1500` | 客车最短路搜索得到的有向走廊边，带 `source_outgoing_edge` / `target_incoming_edge` | 交通流向相关的上游/下游；超过 1500 m 的官方路口对不会进入走廊 |
| 前端拓扑路径（无向） | `frontend/public/intersections/v3/topology-routes.json`（由 SUMO 路网生成） | 官方路口之间的生成路径对，长度以米计 | 地理/路径邻接；包含超过 1500 m 的对，例如 `demo_3`–`demo_5` |

**拓扑邻接 ≠ 上游/下游。** 上游/下游取决于车辆流向：同一对路口在相反流向中角色互换。CosLight `directed_links` 给出客车最短路方向上的 source→target；前端 `topology-routes` 是无向路径。不得把东/西/南/北写成绝对上游。

**【规划功能】** 没有独立的“一跳邻域 API”。AI scope 规划实现应在运行时读取：

1. 当前 preset 路口集合；
2. 事件目标路口/车道；
3. 快照车道 `downstream_lane_ids` 与 Protocol 2.0 `direct_neighbors`；
4. 可选：本文走廊/路径目录作为候选邻域，再按流向过滤。

## Protocol 2.0 直接邻接（当前几乎为空）

该定义要求两路口在 SUMO 上共享出口/进口 edge。因 20 个官方路口多数由中间路段隔开，绝大多数 `direct_neighbors` 为空。

| 路口 | direct_neighbors |
| --- | --- |
| `demo_1` | （空） |
| `demo_2` | `demo_4` |
| `demo_3` | （空） |
| `demo_4` | （空） |
| `demo_5` | （空） |
| `demo_6` | （空） |
| `demo_7` | （空） |
| `demo_8` | （空） |
| `demo_9` | （空） |
| `demo_10` | （空） |
| `demo_11` | （空） |
| `demo_12` | （空） |
| `demo_13` | （空） |
| `demo_14` | `demo_19` |
| `demo_15` | （空） |
| `demo_16` | （空） |
| `demo_17` | （空） |
| `demo_18` | （空） |
| `demo_19` | `demo_14` |
| `demo_20` | （空） |

非空记录仅：`demo_2`→`demo_4`（`demo_4` 列表为空，该关系按 `outgoing ∩ incoming` 计算时不一定对称），以及 `demo_14`↔`demo_19`。`east_dense` 四路口在该定义下 **全部互不为 Protocol 2.0 直接邻接**。

## CosLight 有向走廊（流向相关上下游）

生成配置：`max_corridor_distance_m=1500.0`。超出该距离的官方路口对不会出现在走廊中。

### corridor_1

路口： `demo_2`, `demo_4`

| source | target | 几何方向标签 | 距离 (m) | source 出口 edge | target 进口 edge |
| --- | --- | --- | ---: | --- | --- |
| `demo_2` | `demo_4` | east | 795 | `-57229` | `-57229` |
| `demo_4` | `demo_2` | west | 749 | `-56733` | `-56734` |

表中 `direction` 是 CosLight 生成时的几何方位标签，只描述该有向边相对坐标的朝向，**不能**单独当作交通工程“上游”。对某次事件，若车辆从 source 驶向 target，则 source 是 target 的上游、target 是 source 的下游。

### corridor_2

路口： `demo_5`, `demo_6`, `demo_9`

| source | target | 几何方向标签 | 距离 (m) | source 出口 edge | target 进口 edge |
| --- | --- | --- | ---: | --- | --- |
| `demo_5` | `demo_6` | east | 1296 | `-50820` | `-50819` |
| `demo_6` | `demo_9` | north | 1052 | `-50342` | `-50339` |
| `demo_6` | `demo_5` | west | 1296 | `-57585` | `-57586` |
| `demo_9` | `demo_6` | south | 1053 | `-56620` | `-56623` |

表中 `direction` 是 CosLight 生成时的几何方位标签，只描述该有向边相对坐标的朝向，**不能**单独当作交通工程“上游”。对某次事件，若车辆从 source 驶向 target，则 source 是 target 的上游、target 是 source 的下游。

### corridor_3

路口： `demo_11`, `demo_12`

| source | target | 几何方向标签 | 距离 (m) | source 出口 edge | target 进口 edge |
| --- | --- | --- | ---: | --- | --- |
| `demo_11` | `demo_12` | east | 1196 | `-51252` | `-51253` |
| `demo_12` | `demo_11` | west | 1197 | `-56346.1185` | `-56346` |

表中 `direction` 是 CosLight 生成时的几何方位标签，只描述该有向边相对坐标的朝向，**不能**单独当作交通工程“上游”。对某次事件，若车辆从 source 驶向 target，则 source 是 target 的上游、target 是 source 的下游。

### corridor_4

路口： `demo_14`, `demo_15`, `demo_19`

| source | target | 几何方向标签 | 距离 (m) | source 出口 edge | target 进口 edge |
| --- | --- | --- | ---: | --- | --- |
| `demo_14` | `demo_19` | south | 252 | `-46538` | `-46538` |
| `demo_14` | `demo_15` | north | 377 | `-52559` | `-52560` |
| `demo_15` | `demo_14` | south | 377 | `-46786` | `-46785` |
| `demo_15` | `demo_19` | south | 718 | `-46786` | `-55837` |
| `demo_19` | `demo_14` | north | 250 | `-52216` | `-52216` |
| `demo_19` | `demo_15` | north | 718 | `-57396` | `-52560` |

表中 `direction` 是 CosLight 生成时的几何方位标签，只描述该有向边相对坐标的朝向，**不能**单独当作交通工程“上游”。对某次事件，若车辆从 source 驶向 target，则 source 是 target 的上游、target 是 source 的下游。

### corridor_5

路口： `demo_17`, `demo_18`, `demo_20`

| source | target | 几何方向标签 | 距离 (m) | source 出口 edge | target 进口 edge |
| --- | --- | --- | ---: | --- | --- |
| `demo_17` | `demo_20` | south | 878 | `-57330` | `-57333` |
| `demo_18` | `demo_20` | east | 1090 | `-56831` | `-56836` |
| `demo_20` | `demo_17` | north | 901 | `-57314` | `-57320` |
| `demo_20` | `demo_18` | west | 1090 | `-57073` | `-57077` |

表中 `direction` 是 CosLight 生成时的几何方位标签，只描述该有向边相对坐标的朝向，**不能**单独当作交通工程“上游”。对某次事件，若车辆从 source 驶向 target，则 source 是 target 的上游、target 是 source 的下游。

## 前端拓扑路径邻接（无向）

来源：`topology-routes.json`。下列为每个官方路口的生成路径邻居（按路径长度升序）。

| 路口 | 无向路径邻居（路口, 米） |
| --- | --- |
| `demo_1` | `demo_5` (2931 m), `demo_8` (3711 m) |
| `demo_2` | `demo_4` (796 m), `demo_9` (3584 m) |
| `demo_3` | `demo_6` (2639 m), `demo_5` (3935 m) |
| `demo_4` | `demo_2` (796 m), `demo_9` (3124 m) |
| `demo_5` | `demo_6` (1296 m), `demo_9` (2091 m), `demo_1` (2931 m), `demo_3` (3935 m) |
| `demo_6` | `demo_9` (1052 m), `demo_5` (1296 m), `demo_3` (2639 m) |
| `demo_7` | `demo_10` (4905 m), `demo_8` (8521 m) |
| `demo_8` | `demo_10` (3698 m), `demo_1` (3711 m), `demo_7` (8521 m) |
| `demo_9` | `demo_6` (1052 m), `demo_5` (2091 m), `demo_4` (3124 m), `demo_2` (3584 m) |
| `demo_10` | `demo_8` (3698 m), `demo_7` (4905 m) |
| `demo_11` | `demo_12` (1196 m), `demo_13` (10840 m) |
| `demo_12` | `demo_11` (1196 m), `demo_13` (11962 m) |
| `demo_13` | `demo_20` (3491 m), `demo_18` (4590 m), `demo_11` (10840 m), `demo_12` (11962 m) |
| `demo_14` | `demo_19` (252 m), `demo_15` (377 m) |
| `demo_15` | `demo_14` (377 m), `demo_19` (629 m), `demo_16` (3864 m) |
| `demo_16` | `demo_15` (3864 m), `demo_19` (3940 m) |
| `demo_17` | `demo_20` (878 m), `demo_18` (1778 m) |
| `demo_18` | `demo_20` (1090 m), `demo_17` (1778 m), `demo_13` (4590 m) |
| `demo_19` | `demo_14` (252 m), `demo_15` (629 m), `demo_16` (3940 m) |
| `demo_20` | `demo_17` (878 m), `demo_18` (1090 m), `demo_13` (3491 m) |

## 按预设解读

### east_dense（校园周边场景：demo_3 / 5 / 6 / 9）

**问：east_dense 内部如何连接？**

- Protocol 2.0：内部无直接邻接。
- CosLight 走廊 `corridor_2` 只包含 `demo_5`–`demo_6`–`demo_9`（均 ≤1500 m）。`demo_3` 未进入该走廊，因为到 `demo_5`/`demo_6` 的生成路径分别为约 3935 m / 2639 m，超过走廊阈值。
- 前端路径：内部无向连接为 `demo_3`–`demo_5`、`demo_3`–`demo_6`、`demo_5`–`demo_6`、`demo_5`–`demo_9`、`demo_6`–`demo_9`。没有 `demo_3`–`demo_9` 直接路径对。

**问：demo_5 的直接相邻路口是谁？** 取决于定义：

| 定义 | demo_5 邻居 |
| --- | --- |
| Protocol 2.0 | 无 |
| CosLight 有向走廊 | `demo_6`（双向：东向 1296 m / 西向 1296 m）。`demo_9` 需经 `demo_6` |
| 前端路径（无向） | `demo_6`（1296 m）、`demo_9`（2091 m）、`demo_1`（2931 m，不在 east_dense）、`demo_3`（3935 m） |

**主要上游/下游（east_dense，流向相关）：**

- 若事件车道位于 `demo_5` 东出口驶向 `demo_6` 的方向，则 `demo_5` 为上游、`demo_6` 为下游；`demo_9` 可能是更下游（经 `demo_6` 北向）。
- 若车辆从 `demo_6` 西向回到 `demo_5`，角色对调。
- `demo_3` 与 `demo_5`/`demo_6` 有路径邻接但不是 ≤1500 m 走廊邻接；是否纳入 AI scope 属于规划策略，不能写成“已经是协议直接邻接”。

### west_dense（窄路密网片区：demo_14 / 15 / 19）

**问：west_dense 内部如何连接？**

- Protocol 2.0：仅 `demo_14`↔`demo_19` 共享 edge；`demo_15` 不是协议直接邻接。
- CosLight 走廊 `corridor_4` 三者全覆盖：14↔19 约 250 m，14↔15 约 377 m，15↔19 约 718 m，且均为双向有向边。
- 前端路径：内部三角全连接，另有 `demo_15`/`demo_19` 到 `demo_16`（约 3.9 km，不在 west_dense）。

**主要上游/下游（west_dense，流向相关）：** 使用 CosLight 有向边。例如车辆从 `demo_14` 南向到 `demo_19` 时，`demo_14` 上游、`demo_19` 下游；北向则对调。不得把“南/北”写死为绝对上游。

### xiongan_20

20 个官方路口空间跨度大。CosLight 只生成 5 条短走廊，多数路口对既不是 Protocol 2.0 直接邻接，也不在 1500 m 走廊内。**【规划功能】** 大网 AI takeover 不得默认 20 路口全接管，应使用事件路口 + 当前流向可核验的有限邻域。

## 运行时获取逻辑（规划）

```
topological_neighbor(A, B) =
    Protocol2.direct_neighbors  或  topology-routes 无向路径  或  CosLight 走廊边

upstream(A relative to event movement) =
    沿该 movement 的反方向、能向事件车道/路口送车的邻接路口

downstream(A relative to event movement) =
    沿该 movement 的正方向、接收事件路口放行交通的邻接路口
```

证据不足时，只把事件路口列入 scope，不要编造邻接。

## 缺失与限制

- 仓库没有按每个 origin 标注“主要上游/下游”的单一权威表。
- CosLight 走廊按客车最短路与 1500 m 阈值裁剪，不是全部可达关系。
- 前端路径是无向的，不能单独当上游/下游。
- 快照 `downstream_lane_ids` 是车道级 SUMO connection，不是路口邻接表。
- 本文生成后若路网或 TLS 变更，必须重新提取。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: simulation/sumo/engine/run.py; algorithms/ippo/regression_golden/metadata_xiongan20.json; algorithms/coslight/configs/cloud_topology_v2.json; algorithms/coslight/build_cloud_topology.py; frontend/public/intersections/v3/topology-routes.json; data/maps/sumo/official/tls/official_tls_topology.json; data/maps/sumo/generated/manifests/traffic_manifest.json
   - 用于支持：邻接定义、走廊有向边和预设内部连接。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
