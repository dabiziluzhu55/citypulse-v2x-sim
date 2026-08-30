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

# 信号相位与交通流目录

**【项目事实】** 本文由 `official_tls_topology.json`、`official_tls_plans.json` 与 `tls_manifest.json` 的 `phase_order` / `phase_movements` 提取，代码基线 `main@1331ba87d6cd77e9052953d894a5dc83e1953009`。

**运行时实际合法 phase metadata 优先级高于本 RAG catalog。** Catalog 只用于语义理解、解释和计划生成。实际执行必须重新读取当前会话 Protocol 2.0 路口 `phase_order` 与 `PhaseMetadata`。不得把 SUMO 灯色字符串当作 LLM 控制目标。

## 使用规则

1. LLM / AI plan 只能引用 `official_phase_no`（整数），且必须属于当前运行时 `phase_order`。
2. 官方方案名称（如“东西向直行”）只用于解释，不能代替 phase id。
3. 部分路口相位语义随 period 变化。典型例子：`demo_4` 早高峰 4 相位、平峰 3 相位、晚高峰 4 相位，且 movement/approach 组合不同。
4. 右转政策多数为 `permissive_always`（右转常绿/不受相位置零）；部分路口列出 `phase_controlled_right_turn_approaches`。
5. 掉头政策记录为 `blocked` 时，不得规划 U-turn 专用相位。
6. `tls_manifest.phase_order` 是当前生成产物中的可执行相位集合核对源；若与官方 topology 顶层 `phases` 不一致，以运行时 metadata 为准。

## 全网相位一览（核对 tls_manifest.phase_order）

| 路口 | 进口方向 | 可执行 phase_order | 官方拓扑结构 |
| --- | --- | --- | --- |
| `demo_1` | east, west, north, south | [1, 2, 3, 4] | 顶层 phases |
| `demo_2` | north, south, west | [1, 2] | 顶层 phases |
| `demo_3` | east, west, north, south | [1, 2] | 顶层 phases |
| `demo_4` | east, west, north, south | [1, 2, 3, 4] | 按 period 的 programs |
| `demo_5` | east, west, south | [1, 2] | 顶层 phases |
| `demo_6` | east, west, north, south | [1, 2, 3] | 顶层 phases |
| `demo_7` | east, west, south | [1, 2] | 顶层 phases |
| `demo_8` | east, west, north, south | [1, 2, 3, 4] | 顶层 phases |
| `demo_9` | east, west, north, northeast, south | [1, 2, 3] | 顶层 phases |
| `demo_10` | east, west, south | [1, 2] | 顶层 phases |
| `demo_11` | east, west, north, south | [1, 2, 3, 4] | 顶层 phases |
| `demo_12` | east, west, north, south | [1, 2, 3, 4] | 顶层 phases |
| `demo_13` | east, north | [1] | 顶层 phases |
| `demo_14` | east, north, south | [1, 2] | 顶层 phases |
| `demo_15` | east, west, north, south | [1, 2] | 顶层 phases |
| `demo_16` | east, west, north, south | [1, 2] | 顶层 phases |
| `demo_17` | east, north, south | [1, 2] | 顶层 phases |
| `demo_18` | northeast, southwest, northwest, southeast, auxiliary_east | [1, 2] | 顶层 phases |
| `demo_19` | northeast, southwest, northwest, southeast | [1, 2] | 顶层 phases |
| `demo_20` | east, west, north, south | [1, 2, 3, 4] | 顶层 phases |

## 分路口相位语义

### `demo_1`

- 进口：east: -56907, west: -manual_demo1_missing_arm, north: -57217, south: -56384
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3, 4]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直行 | `through` @ west；permissive through@east |
| 2 | 东西向左转 | `left` @ east, west |
| 3 | 南北向直行 | `through` @ north, south |
| 4 | 南北向左转 | `left` @ north, south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_1_morning_peak` cycle=160：1 G38/Y3/AR2, 2 G32/Y3/AR2, 3 G32/Y3/AR2, 4 G38/Y3/AR2
- `demo_1_off_peak` cycle=140：1 G27/Y3/AR2, 2 G33/Y3/AR2, 3 G33/Y3/AR2, 4 G27/Y3/AR2
- `demo_1_evening_peak` cycle=160：1 G37/Y3/AR2, 2 G34/Y3/AR2, 3 G37/Y3/AR2, 4 G32/Y3/AR2

### `demo_2`

- 进口：north: -57228, south: -56734, west: -51425
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 南北向直行 | `through` @ north, south |
| 2 | 西南向左转 | `left` @ west, south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_2_morning_peak` cycle=80：1 G33/Y3/AR0, 2 G41/Y3/AR0
- `demo_2_off_peak` cycle=80：1 G37/Y3/AR0, 2 G37/Y3/AR0
- `demo_2_evening_peak` cycle=80：1 G45/Y3/AR0, 2 G29/Y3/AR0

### `demo_3`

- 进口：east: -57582, west: -50816, north: -46791, south: -52565
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西左转直行 | `through` @ east, west；permissive left@east,west |
| 2 | 南北左转直行 | `through` @ north, south；permissive left@north,south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_3_morning_peak` cycle=108：1 G55/Y3/AR0, 2 G47/Y3/AR0
- `demo_3_off_peak` cycle=108：1 G55/Y3/AR0, 2 G47/Y3/AR0
- `demo_3_evening_peak` cycle=108：1 G55/Y3/AR0, 2 G47/Y3/AR0

### `demo_4`

- 进口：east: -57186, west: -50333, north: -57229, south: -56732
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3, 4]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直行/南北向直左/南北向直行 | `through` @ south；permissive through@north |
| 2 | 东西向左转/南北向左转/西向直左 | `left` @ south；permissive left@north |
| 3 | 东向直左/南北向直行/西向直左 | `through` @ west；protected extra left@west |
| 4 | 东向直左/南北向左转 | `through` @ east；protected extra left@east |

**【项目事实】** 该路口官方 topology 按 period 给出不同相位组合，不得只用一张表覆盖全部时段。

#### demo_4_morning_peak

| official_phase_no | 主要交通流 |
| ---: | --- |
| 1 | 主运动 `through` @ south；permissive: through@north |
| 2 | 主运动 `left` @ south；permissive: left@north |
| 3 | 主运动 `through` @ west；protected extra: left@west |
| 4 | 主运动 `through` @ east；protected extra: left@east |

#### demo_4_off_peak

| official_phase_no | 主要交通流 |
| ---: | --- |
| 1 | 主运动 `through` @ south；permissive: through@north; left@north,south |
| 2 | 主运动 `through` @ west；protected extra: left@west |
| 3 | 主运动 `through` @ east；protected extra: left@east |

#### demo_4_evening_peak

| official_phase_no | 主要交通流 |
| ---: | --- |
| 1 | 主运动 `through` @ west；permissive: through@east |
| 2 | 主运动 `left` @ west；permissive: left@east |
| 3 | 主运动 `through` @ south；permissive: through@north |
| 4 | 主运动 `left` @ south；permissive: left@north |

官方配时周期（秒，来自 official_tls_plans，仅供理解，执行仍读 runtime）：
- `demo_4_morning_peak` cycle=180：1=南北向直行 G52/Y3/AR2, 2=南北向左转 G30/Y3/AR2, 3=西向直左 G36/Y3/AR2, 4=东向直左 G42/Y3/AR2
- `demo_4_off_peak` cycle=140：1=南北向直左 G57/Y3/AR2, 2=西向直左 G34/Y3/AR2, 3=东向直左 G34/Y3/AR2
- `demo_4_evening_peak` cycle=180：1=东西向直行 G45/Y3/AR2, 2=东西向左转 G30/Y3/AR2, 3=南北向直行 G48/Y3/AR2, 4=南北向左转 G37/Y3/AR2

### `demo_5`

- 进口：east: -50182, west: -56392, south: -57586
- 右转政策：`permissive_always`；掉头：`blocked`
- 相位控制右转进口：`['south']`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直左 | `through` @ east, west；permissive left@east |
| 2 | 南向左右转 | `left` @ south；protected extra right@south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_5_morning_peak` cycle=77：1 G34/Y3/AR0, 2 G37/Y3/AR0
- `demo_5_off_peak` cycle=77：1 G34/Y3/AR0, 2 G37/Y3/AR0
- `demo_5_evening_peak` cycle=77：1 G34/Y3/AR0, 2 G37/Y3/AR0

### `demo_6`

- 进口：east: -56623, west: -50334, north: -50819, south: -57584
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西直左 | `through` @ east, west；permissive left@east,west |
| 2 | 南北左转 | `left` @ north, south |
| 3 | 南北直行 | `through` @ north；permissive through@south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_6_morning_peak` cycle=137：1 G57/Y3/AR0, 2 G26/Y3/AR0, 3 G45/Y3/AR0
- `demo_6_off_peak` cycle=137：1 G57/Y3/AR0, 2 G26/Y3/AR0, 3 G45/Y3/AR0
- `demo_6_evening_peak` cycle=137：1 G57/Y3/AR0, 2 G26/Y3/AR0, 3 G45/Y3/AR0

### `demo_7`

- 进口：east: -51953, west: -46217, south: -51871
- 右转政策：`permissive_always`；掉头：`blocked`
- 相位控制右转进口：`['east', 'south']`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 西向直左、南向直右 | `through` @ west, south；permissive left@west；protected extra right@south |
| 2 | 东向左右转 | `left` @ east；protected extra right@east |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_7_morning_peak` cycle=95：1 G47/Y3/AR0, 2 G42/Y3/AR0
- `demo_7_off_peak` cycle=83：1 G36/Y3/AR0, 2 G41/Y3/AR0
- `demo_7_evening_peak` cycle=90：1 G45/Y3/AR0, 2 G39/Y3/AR0

### `demo_8`

- 进口：east: -54807, west: -57234, north: -57112, south: -57109
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3, 4]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直行 | `through` @ east, west |
| 2 | 东西向左转 | `left` @ east, west |
| 3 | 南北向直行 | `through` @ north, south |
| 4 | 南北向左转 | `left` @ north, south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_8_morning_peak` cycle=110：1 G32/Y3/AR2, 2 G18/Y3/AR2, 3 G28/Y3/AR2, 4 G12/Y3/AR2
- `demo_8_off_peak` cycle=90：1 G25/Y3/AR2, 2 G15/Y3/AR2, 3 G20/Y3/AR2, 4 G10/Y3/AR2
- `demo_8_evening_peak` cycle=120：1 G35/Y3/AR2, 2 G20/Y3/AR2, 3 G30/Y3/AR2, 4 G15/Y3/AR2

### `demo_9`

- 进口：east: -56619, west: -50339, north: -57214, northeast: -50241, south: -56369
- 右转政策：`permissive_always`；掉头：`blocked`
- 相位控制右转进口：`['northeast']`
- 运行时 `phase_order`：`[1, 2, 3]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 南北左转直行 | `through` @ north, south；permissive left@north,south |
| 2 | 东西左转直行 | `through` @ east, west；permissive left@east,west |
| 3 | 东北左转右转 | `left` @ northeast；protected extra right@northeast |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_9_morning_peak` cycle=145：1 G56/Y3/AR0, 2 G48/Y3/AR0, 3 G32/Y3/AR0
- `demo_9_off_peak` cycle=145：1 G56/Y3/AR0, 2 G48/Y3/AR0, 3 G32/Y3/AR0
- `demo_9_evening_peak` cycle=145：1 G56/Y3/AR0, 2 G48/Y3/AR0, 3 G32/Y3/AR0

### `demo_10`

- 进口：east: -50726, west: -57445, south: -50758
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直行 | `through` @ east, west |
| 2 | 东南向左转 | `left` @ east, south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_10_morning_peak` cycle=120：1 G47/Y3/AR2, 2 G63/Y3/AR2
- `demo_10_off_peak` cycle=90：1 G40/Y3/AR2, 2 G40/Y3/AR2
- `demo_10_evening_peak` cycle=120：1 G59/Y3/AR2, 2 G51/Y3/AR2

### `demo_11`

- 进口：east: -57303, west: -51264, north: -57053, south: -56346
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3, 4]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直行 | `through` @ west；permissive through@east |
| 2 | 东西向左转 | `left` @ west；permissive left@east |
| 3 | 南北向直行 | `through` @ south；permissive through@north |
| 4 | 南北向左转 | `left` @ south；permissive left@north |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_11_morning_peak` cycle=170：1 G50/Y3/AR2, 2 G23/Y3/AR2, 3 G52/Y3/AR2, 4 G25/Y3/AR2
- `demo_11_off_peak` cycle=130：1 G40/Y3/AR2, 2 G20/Y3/AR2, 3 G30/Y3/AR2, 4 G20/Y3/AR2
- `demo_11_evening_peak` cycle=176：1 G50/Y3/AR2, 2 G26/Y3/AR2, 3 G52/Y3/AR2, 4 G28/Y3/AR2

### `demo_12`

- 进口：east: -50293, west: -51273, north: -51253, south: -56345
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3, 4]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西向直行 | `through` @ west；permissive through@east |
| 2 | 东西向左转 | `left` @ west；permissive left@east |
| 3 | 南北向直行 | `through` @ south；permissive through@north |
| 4 | 南北向左转 | `left` @ north, south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_12_morning_peak` cycle=170：1 G55/Y3/AR2, 2 G30/Y3/AR2, 3 G50/Y3/AR2, 4 G15/Y3/AR2
- `demo_12_off_peak` cycle=215：1 G65/Y3/AR2, 2 G35/Y3/AR2, 3 G60/Y3/AR2, 4 G35/Y3/AR2
- `demo_12_evening_peak` cycle=180：1 G58/Y3/AR2, 2 G32/Y3/AR2, 3 G53/Y3/AR2, 4 G17/Y3/AR2

### `demo_13`

- 进口：east: -56457, north: -46884
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东进口直行 | `through` @ east |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_13_morning_peak` cycle=75：1 G70/Y3/AR2
- `demo_13_off_peak` cycle=70：1 G65/Y3/AR2
- `demo_13_evening_peak` cycle=75：1 G70/Y3/AR2

### `demo_14`

- 进口：east: -46785, north: -52216, south: -46539
- 右转政策：`permissive_always`；掉头：`blocked`
- 相位控制右转进口：`['east', 'south']`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东、北放行 | `left` @ north；permissive left@east；protected extra through@north; right@east |
| 2 | 南放行 | `through` @ south；protected extra right@south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_14_morning_peak` cycle=75：1 G22/Y3/AR0, 2 G47/Y3/AR0
- `demo_14_off_peak` cycle=75：1 G22/Y3/AR0, 2 G47/Y3/AR0
- `demo_14_evening_peak` cycle=75：1 G22/Y3/AR0, 2 G47/Y3/AR0

### `demo_15`

- 进口：east: -46787, west: -52560, north: -56026, south: -52227
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 南北直左 | `through` @ north, south；permissive left@north,south |
| 2 | 东西直左 | `through` @ east, west；permissive left@east,west |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_15_morning_peak` cycle=115：1 G50/Y3/AR0, 2 G59/Y3/AR0
- `demo_15_off_peak` cycle=115：1 G50/Y3/AR0, 2 G59/Y3/AR0
- `demo_15_evening_peak` cycle=115：1 G50/Y3/AR0, 2 G59/Y3/AR0

### `demo_16`

- 进口：east: -55547, west: -50930, north: -57802, south: -50943
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东西直左 | `through` @ east, west；permissive left@east,west |
| 2 | 南北直左 | `through` @ north, south；permissive left@north,south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_16_morning_peak` cycle=77：1 G34/Y3/AR0, 2 G37/Y3/AR0
- `demo_16_off_peak` cycle=77：1 G34/Y3/AR0, 2 G37/Y3/AR0
- `demo_16_evening_peak` cycle=77：1 G34/Y3/AR0, 2 G37/Y3/AR0

### `demo_17`

- 进口：east: -56184, north: -57320, south: -57329
- 右转政策：`permissive_always`；掉头：`blocked`
- 相位控制右转进口：`['east']`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东进口左右转 | `left` @ east；protected extra right@east |
| 2 | 南北直行转向 | `through` @ north, south；permissive left@north |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_17_morning_peak` cycle=96：1 G58/Y3/AR0, 2 G32/Y3/AR0
- `demo_17_off_peak` cycle=96：1 G58/Y3/AR0, 2 G32/Y3/AR0
- `demo_17_evening_peak` cycle=96：1 G58/Y3/AR0, 2 G32/Y3/AR0

### `demo_18`

- 进口：northeast: -56830, southwest: -57077, northwest: -56004, southeast: -57004, auxiliary_east: E7
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东北、西南放行 | `through` @ northeast, southwest；permissive left@northeast,southwest |
| 2 | 西北、东南放行 | `through` @ northwest, southeast；permissive left@northwest,southeast |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_18_morning_peak` cycle=80：1 G37/Y3/AR0, 2 G37/Y3/AR0
- `demo_18_off_peak` cycle=80：1 G37/Y3/AR0, 2 G37/Y3/AR0
- `demo_18_evening_peak` cycle=80：1 G37/Y3/AR0, 2 G37/Y3/AR0

### `demo_19`

- 进口：northeast: -55837, southwest: -57395, northwest: -52215, southeast: -46538
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东北、西南放行 | `through` @ northeast, southwest；permissive left@northeast,southwest |
| 2 | 西北、东南放行 | `through` @ northwest, southeast；permissive left@northwest,southeast |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_19_morning_peak` cycle=76：1 G35/Y3/AR0, 2 G35/Y3/AR0
- `demo_19_off_peak` cycle=76：1 G35/Y3/AR0, 2 G35/Y3/AR0
- `demo_19_evening_peak` cycle=76：1 G35/Y3/AR0, 2 G35/Y3/AR0

### `demo_20`

- 进口：east: -56836, west: -57067, north: -49964, south: -57333
- 右转政策：`permissive_always`；掉头：`blocked`
- 运行时 `phase_order`：`[1, 2, 3, 4]`

| official_phase_no | 配时名称（官方计划） | 主要交通流（tls_manifest.phase_movements） |
| ---: | --- | --- |
| 1 | 东北、西南左转直行（东、西进口） | `through` @ east, west；permissive left@east,west |
| 2 | 东北、西南左转（东、西进口） | `left` @ east, west |
| 3 | 西北、东南左转直行（北、南进口） | `through` @ north, south；permissive left@north,south |
| 4 | 西北、东南左转（北、南进口） | `left` @ north, south |

官方配时（`official_tls_plans`，绿/黄/全红仅供理解）：
- `demo_20_morning_peak` cycle=120：1 G45/Y3/AR0, 2 G9/Y3/AR0, 3 G45/Y3/AR0, 4 G9/Y3/AR0
- `demo_20_off_peak` cycle=90：1 G33/Y3/AR0, 2 G6/Y3/AR0, 3 G33/Y3/AR0, 4 G6/Y3/AR0
- `demo_20_evening_peak` cycle=110：1 G41/Y3/AR0, 2 G8/Y3/AR0, 3 G41/Y3/AR0, 4 G8/Y3/AR0

## 与执行的关系

CityPulse-Qwen 高层计划可以引用“优先服务某进口直行/左转”。AI Plan Executor 必须把该意图映射到当前 runtime `phase_order` 中的整数 `target_phase`。映射失败则整单 AI plan fallback 到 baseline controller。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: data/maps/sumo/official/tls/official_tls_topology.json; data/maps/sumo/official/tls/official_tls_plans.json; data/maps/sumo/generated/manifests/tls_manifest.json; simulation/sumo/engine/run.py; traffic_control/protocol.py
   - 用于支持：官方相位号、进口方向、运动类型和运行时 phase_order。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
