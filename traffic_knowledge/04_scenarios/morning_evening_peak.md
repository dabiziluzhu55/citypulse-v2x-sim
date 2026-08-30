# 早晚高峰场景

## 项目定义

项目支持 `morning_peak` 和 `evening_peak` 两个 period，并为各路口匹配对应的交通需求与固定信号方案。名称表示项目数据中的场景切片，不自动证明现实日期、真实钟点或需求比例；具体时钟与流量以生成清单和运行会话为准。

## 典型机理

通勤高峰常表现为重复出现的需求增长，方向不均衡可能使部分进口先接近容量。若需求持续超过有效服务能力，排队会增长；高峰结束后，纯需求型队列通常随到达率下降而消散。若需求下降后仍不消散，应排查车道阻塞、下游回溢或信号配置问题。

## 观测与判断

比较同一预设、同一 period、相同窗口和种子的车辆数、平均速度、停止数、等待、排队和通行量。早高峰与晚高峰不可直接用一个绝对阈值判定，方向需求和固定配时方案可能不同。STGCN 预测提供未来约 60 秒车辆数参考，但 fallback 或分布外场景需降低置信度。

## 控制思路

Fixed 是 period 对应方案的基线；SOTL 可响应局部请求变化；Max Pressure 可抑制向拥堵下游放行；IPPO/MAPPO 仅在模型契约、场景适配和评估通过时推荐。高峰控制评价应关注整个窗口及恢复段，而不是只看某个瞬时排队最小值。

## 来源

1. Incorporating Travel-Time Reliability into the CMP
   - 发布机构：FHWA Office of Operations
   - 年份：2014
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop14034/ch1.htm
   - 用于支持：通勤高峰的经常性需求—容量失衡。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: simulation/sumo/scenario.py; data/maps/sumo/official_traffic_demands.json; data/maps/sumo/official_tls_plans.json
   - 用于支持：period 枚举、需求和信号方案关联。

