# SOTL 自组织信号控制

## 定义

SOTL（Self-Organizing Traffic Lights）是一类以局部交通请求驱动相位切换的自组织控制。本项目实现不是对任意文献版本的统称，而是“非当前相位请求积分 + 最小绿 + 近停止线车队保护”的确定性控制器。

## 核心原理

控制器对每个非当前相位累计请求积分 `kappa = 该相位进口车辆数 × 决策间隔`。达到阈值后，选择积分最大的候选相位；若并列，则按当前相位之后的循环顺序选取。当前绿灯未达到最小绿、处于黄灯/清空阶段、已有待切相位，或当前绿方向停止线附近存在需保护的小车队时，不切换。

## 输入与输出

输入包括相位顺序、相位—进口车道映射、车道长度、当前相位、阶段、阶段持续时间、车道车辆数，以及车辆车道位置。输出为各路口可选的 `target_phase`；无动作时返回空建议，由仿真保持当前执行。

## 适用与谨慎场景

SOTL 适合需求随时间变化、希望以局部检测快速响应且不依赖训练模型的场景。其请求积分主要反映车辆数量，不显式计算下游反压；当下游已饱和或路口间距很短时，应谨防继续向拥堵下游放行。阈值、检测范围、最小绿和车队保护参数也需要在目标路网验证。

## 与 Fixed / Max Pressure 的区别

Fixed 不读实时需求；SOTL 以未服务请求积分触发；Max Pressure 比较各转向 movement 的上游队列和下游反压。SOTL 更强调“等待请求何时足够大”，Max Pressure 更强调“哪个相位最能释放网络压力”。

## 当前项目实现

- 实际名称：`sotl`；模块：`traffic_control.sotl`。
- 默认决策间隔 5 s、最小绿 5 s、请求阈值 30 vehicle-seconds、停止线保护范围 25 m、车队上限 3 辆；这些是代码默认值，不是通用标准值。
- 支持预设：注册表未限制，即三个当前预设均允许。
- 当前状态：本地 Protocol 2.0 可运行，不需要 checkpoint。
- 角色：baseline controller。主要看上游请求，密网或扰动后需警惕向已饱和下游继续放行。Qwen 不在算法菜单中“推荐 SOTL”。

## 来源

1. Gershenson, Self-Organizing Traffic Lights
   - 发布机构：Complex Systems / arXiv 原始论文
   - 年份：2005
   - URL：https://arxiv.org/abs/nlin/0411066
   - 用于支持：以局部规则自组织适应变化交通的算法思想。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/sotl.py; traffic_control/registry.py
   - 用于支持：项目精确积分规则、默认参数、输入输出和运行状态。

