# CARLA 联合仿真工具链

## 需求与问题

本项目面向雄安新区"城市大脑"的车路云一体化协同管控算法与仿真平台研究（XH-202613）：以真实城区 20 个路口为研究对象，需要在一个统一的仿真平台上完成交通流仿真、信号配时验证与路侧感知算法（视觉、激光雷达）的数据采集与评测。

赛题给定的 20 个路口组合形成一张覆盖约 200km²（46×58km）的 SUMO 大路网，而整个"城市大脑"场景对仿真平台提出了两个相互矛盾的要求：

- **大规模交通流仿真**：全网车辆的路由、跟驰、信号灯配时必须与SUMO同步
- **传感器与高保真仿真**：算法研究需要从路口视角拍摄 RGB 图像、采集激光点云等数据

然而 CARLA 受限于设备性能，只能渲染小范围典型场景地图；SUMO 则不具备 3D 渲染与传感器模型。因此本模块采用 **SUMO 与 CARLA 联合仿真**的方案：SUMO 负责全网交通流仿真，CARLA 负责小范围典型场景的高保真渲染与传感器数据导出。

直接使用 CARLA 官方 Co-Simulation 工具（`Co-Simulation/Sumo`，随 CARLA 0.9.16 发布）存在三个无法回避的问题，成为本模块优化的出发点：

1. **无空间过滤，全网车辆全部涌入 CARLA**：官方 `SimulationSynchronization` 会把路网中**每个**出发的 SUMO 车辆都 spawn 进 CARLA。在 46×58km 的全网规模下，CARLA 端 actor 数量爆炸，`world.tick()` 延迟急剧上升，小场景渲染失去意义。
2. **Traffic Manager 崩溃**：官方工具无条件创建 CARLA Traffic Manager，本项目实测在加载部分地图时该调用确定性触发 libcarla 客户端 SIGSEGV（堆损坏），联仿无法稳定运行。
3. **无数据导出能力**：算法模块需要路侧视角的 RGB 帧、激光点云与 KITTI 格式数据集，官方工具完全不提供，需要自行实现一整套可扩展的数据导出框架。

本模块的目标即围绕以上三点展开：在复用官方联仿工具的基础上，实现"**SUMO 全网仿真 + CARLA 小场景渲染**"的联合仿真桥，并提供可插拔、零阻塞、异常隔离的路侧感知数据导出框架，使算法模块可以一键采集训练与评测所需数据。

## 项目结构

```
carla/
├── data_export/                # 数据导出框架（可插拔、零阻塞）
│   ├── base.py                 #   Exporter 抽象基类 + @register 注册表
│   ├── config.py               #   导出配置加载/校验 + BLUEPRINTS 类型映射
│   ├── sensors.py              #   SensorFarm：传感器 spawn + writer 线程池
│   ├── manager.py              #   ExporterManager：生命周期编排 + 异常隔离
│   ├── calibration.py          #   相机标定数学（内参/外参，惰性依赖 carla）
│   ├── context.py              #   ExportContext + FrameRegistry（按 tick 索引）
│   ├── output.py               #   输出目录布局 + JSONL/原子写
│   ├── selfcheck.py            #   离线自检（无需 CARLA 即可运行）
│   └── exporters/               #   内置导出器：rgb_camera / lidar / kitti / manifest
│
├── config/                     # 配置目录
│   ├── toolchain.json          #   SUMO/CARLA 环境路径统一配置
│   ├── taz.json                #   TAZ 典型场景区域配置（多路口组合）
│   ├── intersections.json      #   demo 路口名 → junction ID 映射
│   ├── export.example.json     #   数据导出配置示例
│   └── export_configs/          #   每地图导出配置（<地图名>.json）
│
├── xml2odr/                    # 地图裁剪模块（run_xml2odr.py 的包）
│   ├── cli.py                  #   命令行入口（单路口/批量/TAZ 三模式）
│   ├── graph_model.py          #   数据模型（Lane/Edge/Junction/TlLogic…）
│   ├── net_parser.py           #   SUMO .net.xml 流式解析器（iterparse）
│   ├── topological_clipper.py  #   BFS 拓扑距离裁剪 / TAZ MST 多级展开
│   ├── net_writer.py           #   输出裁剪后的 .net.xml
│   ├── netconvert_runner.py    #   调用 netconvert 转换为 .xodr
│   └── batch_clip.py           #   批量处理模块
│
├── run_xml2odr.py              # 地图裁剪入口：裁剪 + 转换为 .xodr
├── run_cosimulation.py         # 联仿核心：SUMO + CARLA 桥接与场景过滤
├── validate.py                 # 地图联仿有效性校验
├── tp.py                       # spectator 快速传送（联仿运行中巡路）
├── spectator_coords.py         # 读取 spectator 位姿；--save 记录传感器点位
├── plan_lidar_points.py        # 按 TAZ 自动布点路侧 lidar
├── bin2pcd.py                  # KITTI .bin 点云 → PCD（零拷贝直写）
└── toolchain_env.py             # 统一环境路径解析
```

联仿桥复用父目录 `Sumo/` 下 CARLA 官方 Co-Simulation 工具包中的 `SumoSimulation`（SUMO 管理）、`CarlaSimulation`（CARLA actor 管理）、`SimulationSynchronization`（双向车辆与信号灯同步）三个类，所有优化通过**构造参数 + 实例属性注入 + monkey-patch** 实现，不 fork 官方源码（详见下文「对官方联仿工具的优化」）。

### 对官方联仿工具的优化

#### 优化总览

| 环节 | 官方原版行为 | 本项目行为 |
|---|---|---|
| 车辆 spawn | 全网车辆全部 spawn 进 CARLA，无空间过滤 | **场景框门控**：仅场景内车辆渲染，场景外车辆留在 SUMO 运行 |
| Traffic Manager | `SimulationSynchronization` 构造时无条件创建 | **桩掉**（`COSIM_NO_TM=0` 可恢复）：车辆全由 SUMO 驱动，零功能损失 |
| 信号灯 | 默认 `tls_manager='none'`（双方灯独立） | `tls_manager='sumo'`：CARLA 灯关闭，状态每步从 SUMO 同步 |
| 坐标偏移 | 使用 SUMO `netOffset`（tmerc 原点，如 TotalMap 为 -5488893.66, -13133184.77） | **强制归零 + fail-fast 自检**：CARLA 地图与 SUMO 网同一内部坐标，须用恒等映射 `carla=(sumo_x, -sumo_y)` |

#### ① 联仿过滤层：场景框 + spawn 门控（核心创新）

**问题。** 官方 `SimulationSynchronization.tick()` 对每个 SUMO 新出发车辆无条件调用 `CarlaSimulation.spawn_actor()`，全网 46×58km 的车辆规模下，CARLA 端 actor 数量不可控，同步 tick 被拖垮。联仿的前提是**将 CARLA 的渲染范围严格限制在小场景内**。

**方案。** 过滤层由三个相互配合的机制构成（均位于 `run_cosimulation.py` 的 `CoSimulationBridge` 类）：

1. **场景框计算 `_compute_scene_box()`**：从 CARLA 地图的路网拓扑（`world.get_map().get_topology()`）取全部道路端点坐标，计算包围盒并**外扩 300m**（`pad = 300.0`）——让接近边界的车辆在越界前就被"拾取"进入场景；支持 `--scene-box X0,Y0,X1,Y1` 手动覆盖；地图无路网拓扑时自动关闭过滤（降级为官方行为）。
2. **spawn 门控 `_install_spawn_gate()`**：monkey-patch 替换官方 `CarlaSimulation.spawn_actor`。目标 transform 落在场景框外时直接返回官方常量 `INVALID_ACTOR_ID`（-1），否则走原始实现。
3. **场景成员检查 `_enforce_scene_membership()`**：每 20 步（0.05s 步长下约 1s）执行一次：遍历 `SimulationSynchronization.sumo2carla_ids`，SUMO 坐标出框的车辆 destroy 其 CARLA actor；同时遍历 `traci.vehicle.getIDList()`，把入框但尚未 spawn 的车辆补 spawn（仍经过门控，双重保险）。

**原理。** 官方 `SimulationSynchronization.tick()` 把失败 spawn（返回 `INVALID_ACTOR_ID`）视为"未跟踪"车辆——该车继续保留在 SUMO 订阅中，不报错、不影响其它车辆。门控正是利用了这一语义，实现了精确的"全网仿真 / 小场景渲染"切分：

> The official tick treats a failed spawn (INVALID_ACTOR_ID) as "not tracked" and keeps the vehicle running in SUMO only — exactly the desired whole-net / small-scene split.

**效果。** 场景外车辆在 SUMO 中正常运行，进出场景时 CARLA actor 动态 spawn/destroy；CARLA 端 actor 数量被约束在场景框内（状态栏实时显示 `actors=N`），tick 延迟稳定，小场景渲染与全网仿真互不拖累。

#### ② Traffic Manager 去除

官方 `SimulationSynchronization.__init__` 无条件执行 `traffic_manager = client.get_trafficmanager(); traffic_manager.set_synchronous_mode(True)`。本项目的做法是在构造同步桥**之前**用桩替换 `carla.Client.get_trafficmanager`（`_patch_out_traffic_manager()`），桩仅实现官方代码用到的 `set_synchronous_mode()` 空方法。

去除理由分两层：

- **崩溃规避（实测）**：官方 TM 创建在本项目加载某些地图时确定性触发 libcarla 客户端 SIGSEGV（堆损坏，faulthandler 已实证崩溃点），联仿无法稳定运行；
- **设计不需要（架构）**：本项目采用 SUMO 主导模式——车辆路由、跟驰、转向全部由 SUMO 计算，红绿灯经 `tls_manager='sumo'` 从 SUMO 同步，TM 创建后仅被调用一次 `set_synchronous_mode(True)` 即被丢弃，桩掉**零功能损失**。

环境变量 `COSIM_NO_TM=0` 可关闭桩、恢复真实 TM 行为（默认 `COSIM_NO_TM=1`）。

#### ③ 工程性修正

| 修正 | 说明 |
|---|---|
| **坐标偏移归零** | 官方在构造时设置 `BridgeHelper.offset = sumo.get_net_offset()`；SUMO 1.12.x 返回的是路网 `<location netOffset>`（tmerc 原点，TotalMap 为 -5488893.66, -13133184.77），而非 0。本项目 CARLA 地图与 SUMO 网位于**同一内部坐标**，因此构造后强制改回 `(0.0, 0.0)` 并加 fail-fast 自检——不复位则所有车辆被放置在离场景约 550 万米处，`actors=0` |
| **复用 client/world** | 官方 `CarlaSimulation` 自建连接（2s timeout）；本项目复用已加载的 client/world（`load_world()` 已调用），并将 timeout 提到 30s（复杂 RoadRunner 场景单次 tick 可能超过 2s），同时按已加载地图重建信号灯注册表 |
| **同步步长三级来源** | `--step-length` 显式参数 > sumocfg 中 `<time><step-length>` > 默认 0.05s，三者合一：SUMO 启动、CARLA `fixed_delta_seconds`、导出帧率对齐全部跟随同一值；步长与导出 fps 对齐关系自动提示 |
| **spectator 自动定位** | `--junction` 指定目标路口时，查询 SUMO 路口坐标换算为 CARLA 坐标，将观察摄像机置于路口正上方 10m 垂直俯视（高度按路面 waypoint 高程计算，保证始终在道路上）；不指定时聚焦地图中心，并自动跟随场景内第一辆生成的车 |
| **可观测性** | 每仿真秒输出一行彩色状态栏 `[地图名] sim=… wall=… rt=… fps=… actors=…`；vType 缺失警告按类型去重；500km 级坐标失配运行时自检；tick 异常容错（break 而非崩溃） |

### 数据导出层架构

#### 设计目标

数据导出层的定位是：**联仿运行的同时，按路侧固定点位采集算法所需数据**（RGB 帧、激光点云、KITTI 格式），并让这一能力可插拔、方便扩展。

#### 总体架构

```
CARLA 服务器（传感器回调，客户端线程）
   │  listen() 回调：分配序号 seq → put_nowait(有界队列, maxsize=4)
   ▼
有界队列 ──▶ writer 线程 × N（write_threads，默认 2，可 1-16）
   │           并行 PNG/PLY/.bin 编码与落盘（完成可能乱序，无妨）
   ▼
仿真主循环 on_sim_tick ──▶ 按捕获序号严格顺序释放结果（当前序号未完成则
                               游标停住、下个 tick 再试；丢弃/失败记为 no_data）
   ▼
per-sensor manifest.jsonl（由消费者单线程写，行序稳定）
   ▼
teardown → 数据导出器先停、manifest 最后 → meta.json（原子写）
```

核心组件（`data_export/` 包）：

- **`Exporter` 抽象基类**（`base.py`）：生命周期契约 `Cls(kind, specs)` → `setup(ctx)`（CARLA/SUMO 就绪后）→ `on_sim_tick(export_frame, sim_time)`（每个同步步一次）→ `teardown()`（CARLA 同步关闭前）。四项约束：`__init__` 不碰 CARLA/文件系统；`setup` 可失败（manager 捕获并禁用该导出器，仿真继续）；`on_sim_tick` 必须廉价（纯内存、<1ms），所有文件 I/O 在 writer 线程；`teardown` 永不抛异常。
- **`SensorFarm`**（`sensors.py`）：spawn/destroy CARLA 传感器 actor，每传感器一个 `queue.Queue(maxsize=4)` 有界队列与 N 个 daemon writer 线程。队列满时**丢弃最新帧**（drop-newest）并计数，不阻塞回调。
- **`ExporterManager`**（`manager.py`）：生命周期编排与异常隔离——任一导出器连续 5 次异常（`MAX_CONSECUTIVE_ERRORS = 5`）自动禁用并告警；`setup` 失败的导出器被踢出活动列表；全部失败则打印 "data export disabled, simulation continues"，仿真不受影响。
- **`ExportContext` / `FrameRegistry`**（`context.py`）：运行时共享上下文（world/client/步长/输出目录/write_threads 等）与按 tick 索引的帧登记表。

#### 可插拔机制

本工具链提供了扩展性良好的数据导出框架，新增一种导出类型只需三步，**`run_cosimulation.py` 零改动**：

1. 在 `data_export/exporters/` 下新建模块，类上标注 `@register("your_kind")`；
2. 在 `exporters/__init__.py` 的 import 列表加一行；
3. 在导出配置的 `sensors[]` 中用 `"type": "your_kind"` 引用。

`kind` 同时是配置字段值；`BLUEPRINTS` 映射表（`config.py`）负责 kind → CARLA blueprint id（`rgb_camera → sensor.camera.rgb`、`lidar → sensor.lidar.ray_cast`），新增传感器类型只需扩展该映射。已内置四种导出器：`rgb_camera`（PNG 帧 + calibration.json）、`lidar`（PLY 点云）、`kitti`（KITTI 布局数据集）、`manifest`（运行级索引，有任一导出器活跃时自动附带）。`kitti` 是"消费型"导出器：没有自己的传感器，而是把配置中的 rgb_camera + lidar 规格改为前缀 `kitti_` 后生成副本。

#### 线程模型与性能

正确性三不变量（`sensors.py` 模块 docstring）：

- 文件名是捕获序号的**纯函数**（`frame_%06d.png` / `.ply`），任何 worker 计算都无竞争，乱序完成无害；
- per-sensor manifest 由**消费者单线程**写，JSONL 行序与捕获顺序严格一致，原子 flush（崩溃最多丢一行）；
- 有界队列**丢最新帧**并计数（`drops`），运行级 manifest 中被丢帧标记为 `no_data`。

PNG/PLY 编码是纯 CPU 且帧间完全独立，多核服务器上可通过增大 `output.write_threads`（默认 2，合法范围 1-16）提升并行编码吞吐——终端出现 `sensor queue full — dropping frame` 告警时，增大它是第一调优手段；若 CPU 已饱和则按"减相机数 → 降分辨率（1280×720）→ 降 fps"顺序处理。

**操作方式**：`write_threads` 不是命令行参数，写在**导出配置文件**的 `output` 对象中（与 `fps`、`export_dir` 并列）。编辑 `config/export_configs/<地图名>.json`：

```json
{
  "version": 1,
  "output": {"fps": 30, "export_dir": "../../data/exports", "write_threads": 4},
  "sensors": [/* 相机与 lidar 点位，保持不变 */]
}
```

每个传感器启动 N 个并行编码 worker（如 3 台相机 × 4 = 12 个编码线程）；不写该字段时默认 2，超出 1-16 范围配置校验直接报错。用 `python run_cosimulation.py --check-env` 可提前校验配置合法性。

#### 标定与 KITTI

路侧相机静止，内外参全程恒定，因此标定文件在 setup 时**一次性写出**：

- **`calibration.json`**（每台相机）：内参 `K` 按 CARLA 针孔模型 `fx = fy = (w/2) / tan(fov/2)`、主点取图像中心（正方形像素）；外参 `T_wc`（相机局部系 → 世界系）直接取自 `carla.Transform.get_matrix()`，`T_cw` 用 `get_inverse_matrix()`（`P_cam = T_cw · P_world`）。4x4 齐次矩阵、行主序嵌套列表，直接 JSON 序列化；坐标系为 CARLA 左手系（局部 x 前 / y 右 / z 上）。
- **KITTI 布局**（`--export kitti`）：每台 lidar 一个 `velodyne_<名>/*.bin`（x/y/z/intensity 小端 float32，每点 16 字节，直接取自 `LidarMeasurement.raw_data` 零拷贝写出），每台相机一个 `image_<名>/*.png`，外加 setup 时一次写出的 `calib.txt`：`P_<cam>`（3x4 投影矩阵 `K @ [R_cw | t_cw]`）、`R0_rect`（单位阵，P 已含校正）、`Tr_velo_<lidar>_to_cam_<cam>`（3x4，`[T_cw_cam @ T_wc_velo][:3]`）。同步模式下**同帧号 PNG 与 .bin 来自同一 tick**（可用 manifest 行核对）。坐标系声明：矩阵为 CARLA 左手系数值，未映射到标准 KITTI 右手相机系，下游消费需自行施加文档化的旋转变换。
- **`bin2pcd.py`**：KITTI `.bin` 与 PCD binary 数据布局完全一致（16 字节/点），转换即"文本头部 + 原字节直写"，零拷贝，转换结果可直接用 CloudCompare / Open3D 查看。

#### 离线自检

`python -m data_export.selfcheck` 在**无需 CARLA** 的环境下运行 12 项检查：导出器注册表完整性、配置校验、fps/步长对齐、输出布局、标定数学复算（含 `T_cw @ T_wc == I` 断言）、spectator --save 管线、布点坐标、bin→PCD、并行 writer 乱序/丢帧不死锁、端到端生命周期（spawn → tick → 落盘 → teardown → meta.json）。包内所有模块无顶层 `import carla`（惰性按需导入），保证自检与早期导入在无 CARLA 机器上可用。

#### 配套工具

| 工具 | 作用 |
|---|---|
| `spectator_coords.py --save` | 在 CARLA 窗口中把观察摄像机移到目标位置后一键记录点位，自动写入 `config/export_configs/<地图>.json`。读数原理：`get_transform()` 返回的是客户端缓存的最近一帧位姿，脚本先 `wait_for_tick` 等新帧再读，**绝不调用 `world.tick()`**（第二个客户端 tick 会让 SUMO↔CARLA 失同步）；原点读数（误操作）拒绝保存 |
| `plan_lidar_points.py` | 按 TAZ 组自动布点：每个路口中心正上方一台 64 线 360° lidar（默认 range 50m，覆盖"路口本体 + 每条路延伸 ~30m"），写入导出配置，与相机条目共用 `sensors[]`；支持 `--dry-run --print-coverage` 预览 |
| `tp.py` | 联仿运行中快速传送观察摄像机到任意路口或坐标（不 tick，同步安全），方便多路口巡检 |

## 环境配置

### 依赖清单

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | 3.8+ | 联仿桥与数据导出框架运行环境 |
| SUMO | 设置 `SUMO_HOME`，`netconvert` 可用 | 全网交通流仿真（含信号灯配时） |
| CARLA | 0.9.16 源码版或打包版，**Linux + GPU** | 3D 渲染与传感器仿真；本项目运行环境为 Linux GPU 服务器 |

**重要**：CARLA 须以**源码版的独立游戏进程模式或打包版**启动。UE4 Editor 普通模式下开启同步模式会导致渲染异常（地图消失、无响应）。

联仿桥动态定位并复用父目录 `Sumo/` 下 CARLA 官方 Co-Simulation 工具包（`SumoSimulation` / `CarlaSimulation` / `SimulationSynchronization`），须保证 `CARLA_ROOT` 指向完整 CARLA 源码树（官方工具包位于 `<CARLA_ROOT>/Co-Simulation/Sumo`）。

### 路径统一配置（config/toolchain.json）

`run_cosimulation.py`、`validate.py`、`spectator_coords.py` 等统一经 `toolchain_env.py` 解析 SUMO/CARLA 路径，优先级：**CLI 参数 > 环境变量（`SUMO_HOME`/`CARLA_ROOT`）> `config/toolchain.json` > PATH**。

```json
{
  "sumo_home": "/usr/share/sumo",
  "carla_root": "/home/kemove/devdata1/zrl/software/carla-0.9.16-src",
  "sumo_toolkit_dir": "/home/kemove/devdata1/zrl/software/carla-0.9.16-src/Co-Simulation/Sumo",
  "exports_dir": "../../data/exports",
  "maps_xodr_dir": "../../data/maps/xodr",
  "maps_carla_dir": "../../data/maps/carla",
  "totalmap_net": "../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml"
}
```

### 环境自检

```bash
python run_cosimulation.py --check-env
```

`--check-env` 不启动仿真，全量预检：CARLA Python API 与服务器连通性、可用地图列表、SUMO/traci/sumolib、官方联仿工具包、netconvert 二进制、导出配置 schema、磁盘占用估算、步长与 fps 对齐提示。联仿启动前建议先跑一遍。

## 使用手册

### 地图准备工作

联仿要求 CARLA 中已导入**预加工的小范围地图**。标准链路：

```
SUMO 大路网 TotalMap.net.xml
   → run_xml2odr.py 裁剪（单路口或 TAZ 组合）
   → .xodr (OpenDRIVE)
   → RoadRunner 加工（场景建模、资产导入）
   → CARLA Unreal 项目导入
```

```bash
# 单路口裁剪（按 junction ID，沿道路拓扑距离 100m）
python run_xml2odr.py --junction 4427 --dist 100 -o demo_1.xodr

# TAZ 典型场景裁剪（多路口组合，最小生成树保证路口间连通）
python run_xml2odr.py --taz-config config/taz.json --taz EastZone --dist 150 -o TAZ_1.xodr
```

完整参数（批量模式、`--keep-net`、`--skip-netconvert` 等）见 `README.md`。

**导入 CARLA 前先校验**。RoadRunner 重导出地图后、导入 CARLA 之前，用 `validate.py` 将加工后 `.xodr` 与裁剪子网 `.net.xml` 比对，提前发现坐标偏移、路口拓扑缺失等问题（CARLA 导入自定义地图耗时较久，可显著减少返工）：

```bash
python validate.py EastZone --json     # A 坐标对齐 / B 路口拓扑 / C 官方 round-trip
```

注意：该校验只覆盖路网基本拓扑，是联仿可行的**必要条件而非充分条件**；退出码 0/1/2/3 分别对应 OK / 警告 / 错误 / 运行故障。

### 联合仿真启动

```bash
python run_cosimulation.py \
    --sumocfg ../../data/maps/sumo/generated/traffic/global/off_peak/simulation.sumocfg \
    --carla-map WestZone \
    --max-time 60            # 仿真时长（秒），默认 3600

# 可选：--junction <路口ID> 启动后自动定位到路口正上方俯视
#       --sumo-gui          同时打开 SUMO GUI
#       --carla-host <IP>   CARLA 在远程机器
#       --step-length 0.1   同步步长（默认读 sumocfg，未配置则 0.05）
```

启动后终端每秒输出一行彩色状态：

```
[WestZone] sim=   10.0s  wall=     8s  rt= 0.8x  fps=   20  actors=  27
```

| 字段 | 含义 |
|---|---|
| `[地图名]` | CARLA 实际加载的地图（`world.get_map().name`） |
| `sim` / `wall` | 仿真时间 / 实际流逝时间（秒） |
| `rt` | 实时倍率，>1 表示仿真慢于真实时间 |
| `fps` | CARLA 每秒帧数 |
| `actors` | 当前场景内同步的车辆数 |

**设计原则**：SUMO 控制所有车辆行为与红绿灯状态，CARLA 仅负责 3D 渲染；SUMO 大地图中的车辆不在 CARLA 小场景内时，其 spawn 被场景框门控拒绝，车辆继续在 SUMO 中正常运行，进出场景时动态 spawn/destroy。

**常见疑问**：联仿未运行时直接进入 CARLA 地图可能什么都看不见——观察摄像机默认位于 World Origin，而场景大概率不在此处；启动联仿后摄像机自动定位到指定路口（`--junction`）或第一辆生成车辆上方。

### CARLA数据导出

**三步工作流：选点位 → 配置 → 启动导出。**

**① 选点位**（联仿运行中或单独运行均可）：在 CARLA 窗口把观察摄像机移到目标路侧位置（WSAD + 鼠标 + QE，Shift 加速），然后：

```bash
python spectator_coords.py --once --save --save-name cam_01
```

自动写入 `config/export_configs/<当前地图名>.json`（`--save-name` 省略时自动递增 `cam_01, cam_02, …`；同名再次保存就地替换）。

lidar 也可按 TAZ 自动布点：`python plan_lidar_points.py --taz WestZone --dry-run --print-coverage` 预览后正式写入。

**② 配置**（`config/export.example.json` 为完整示例）：`sensors[]` 中每条为 {name, type, transform（绝对世界位姿）, 传感器参数}，RGB 相机与 lidar 条目共用同一数组。

**③ 启动导出**（`--export` 列出导出器，`--export-config` 指定配置，省略时自动按 `--carla-map` 查找同名配置）：

```bash
python run_cosimulation.py \
    --sumocfg ../../data/maps/sumo/generated/traffic/global/off_peak/simulation.sumocfg \
    --carla-map WestZone --max-time 60 \
    --export rgb_camera,lidar

# 仅导出 KITTI 格式数据集（.bin + .png + calib.txt）
python run_cosimulation.py \
    --sumocfg … --carla-map WestZone \
    --export kitti --export-config config/export_configs/WestZone.json --max-time 60
```

输出目录：

```
../../data/exports/<地图名>/<YYYYmmdd-HHMMSS>/
├── meta.json            # 运行汇总：帧数/丢弃/错误/rt 统计
├── run_config.json      # 生效配置副本（含解析后的传感器属性）
├── manifest.jsonl       # 运行级逐 tick 索引（含天气采样）
└── sensors/
    ├── cam_01/frame_000001.png … + manifest.jsonl + calibration.json
    ├── velodyne_<lidar>/*.bin …（kitti 模式）
    └── …
```

**性能调优**：出现 `sensor queue full — dropping frame` 时增大 `output.write_threads`（默认 2，配置方法见「线程模型与性能」节）；持续掉速（`rt < 0.8` 且状态栏出现 `export=BEHIND`）时按"减相机数 → 降分辨率 → 降 fps → 增大步长"顺序调整。多台 lidar 并行时 CARLA 每台独立 ray cast，fps 会明显下降，建议按需启用。

完整参数说明与扩展新导出类型的指引见 `README.md`（第 7 节）与 `data_export/selfcheck.py`（离线自检）。
