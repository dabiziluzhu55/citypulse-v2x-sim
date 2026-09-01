# 校园通学片区场景知识

## 与当前预设的关系

**【项目事实】** Backend `east_dense` 的 catalog 标签已是“校园周边场景”，路口为 `demo_3`、`demo_5`、`demo_6`、`demo_9`。前端 UI 仍可能显示“东部密集区场景”。这是业务标签，不是实测学校出入口或接送流量标定。四路口直线距离约 0.8–2.7 km，几何上不是窄路密网；`demo_3` 进口路段可达 1–2 km 级。生成需求含机动车和电动自行车，**不含行人过街**。

## 交通需求特征

学校上学、放学时段可能出现短时间方向集中的接送需求；步行、非机动车、校车和机动车在校门、过街设施、临停区附近交织。接送区是儿童步行/骑行与车辆、公交或校车产生冲突的重点位置。具体开始时间、持续时长、方式分担率和交通量必须来自场景配置或实测，不能预设。

## 可能的数据表现

靠近出入口的进口车道车辆数、停止数和等待短时上升；相邻路口随后出现排队增长；到校与离校的方向性可能相反。机动车指标不能完整代表行人和非机动车风险，如果 SUMO 场景未建模行人或过街需求，必须声明盲区。

## 控制思路

先保障相位安全、最小绿和行人/非机动车过街约束，再谈机动车效率。未建模行人时必须声明安全盲区。无扰动时 east_dense 仍用用户选择的 baseline controller；IPPO/MAPPO 在此预设是 20 路口模型的零样本。活动需求可用 `major_event_opening/closing` 构造实验，但不能等同真实上下学流量。**【规划功能】** 校园标签不自动扩大 AI 接管范围。

## 检索与决策提示

输出需区分：Backend 校园标签、前端文案、观测到的交通变化和未建模的行人。不得仅为提高机动车通行量而建议削减未建模的安全清空或过街时间。

## 来源

1. GA/T 1215-2025《中小学与幼儿园周边道路交通组织设计与交通设施设置规范》
   - 发布机构：公安部；全国道路交通管理标准化技术委员会归口
   - 年份：2025
   - URL：https://ywtb.mps.gov.cn/gabzh/portal/stdDetail/313654
   - 用于支持：校园周边多方式交通组织的规范背景；2026-05-01 实施，代替 GA/T 1215-2014。
2. FHWA Resident's Guide, Section One
   - 发布机构：美国联邦公路管理局
   - 年份：2014
   - URL：https://highways.dot.gov/safety/pedestrian-bicyclist/residents-guide-creating-safer-communities-walking-and-biking/section
   - 用于支持：学校接送区车辆、行人、自行车与公交冲突的风险背景。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/scenario/presets.py; backend/app/schemas/events.py; traffic_control/ippo/aliases.py; traffic_control/mappo/aliases.py
   - 用于支持：东部预设路口、活动注入与零样本模型边界。

