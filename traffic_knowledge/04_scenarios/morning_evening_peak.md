# 早晚高峰场景

## 项目定义

**【项目事实】** 项目支持 `morning_peak` 和 `evening_peak`。官方时钟分别为 07:00–09:00 与 17:30–19:30，并匹配对应需求与固定信号方案。名称是项目数据切片，不自动证明某一现实日期的真实比例。生成制品的观测 PCU 见 `simulation_demand_catalog.md`；API 会话流量以当次运行为准。

## 典型机理

通勤高峰常表现为重复出现的需求增长，方向不均衡可能使部分进口先接近容量。若需求持续超过有效服务能力，排队会增长；高峰结束后，纯需求型队列通常随到达率下降而消散。若需求下降后仍不消散，应排查车道阻塞、下游回溢或信号配置问题。

## 观测与判断

比较同一预设、同一 period、相同窗口和种子的车辆数、平均速度、停止数、等待、排队和通行量。早高峰与晚高峰不可直接用一个绝对阈值判定，方向需求和固定配时方案可能不同。NarrowNet-TDP 提供未来约 60 秒路口车辆数参考；`fallback=true` 或局部预设输入稀疏时需降低置信度。

## 控制思路

无扰动时由用户选择的 baseline controller 运行。高峰评价应关注整个窗口及恢复段。**【规划功能】** 高峰中的事故或占道才触发 AI 局部接管，Qwen 不负责在算法之间做选择。

## 来源

1. Incorporating Travel-Time Reliability into the CMP
   - 发布机构：FHWA Office of Operations
   - 年份：2014
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop14034/ch1.htm
   - 用于支持：通勤高峰的经常性需求—容量失衡。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: simulation/sumo/engine/scenario.py; data/maps/sumo/official/traffic/official_traffic_demands.json; data/maps/sumo/official/tls/official_tls_plans.json; backend/app/services/prediction_runtime.py
   - 用于支持：period 枚举、需求和信号方案关联。

