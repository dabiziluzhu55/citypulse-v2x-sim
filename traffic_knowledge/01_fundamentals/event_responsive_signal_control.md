---
information_type: traffic_expertise
status: current
applicable_events:
  - accident
  - lane_closure
  - speed_limit
  - major_event_opening
  - major_event_closing
priority: high
---

# 事件响应信号控制

**【交通专业知识】** 本文整理突发扰动与计划性活动下的信号控制原则，来自 FHWA 公开手册。它 **不是** 项目事实，不能覆盖 CityPulse 运行时快照、合法相位或 Protocol 2.0 能力。

## 非经常性拥堵会改变配时目标

事故、施工、临时限速、大型活动和疏散会造成非经常性拥堵。此时交通模式不同于日常高峰配时。FHWA Traffic Signal Timing Manual 指出：特殊事件、施工、事故等条件下的交通模式与正常条件不同，机构需要相应的信号管理计划；目标可包括在事件期间维持或提高走廊通行能力、为优先流向提供更多绿灯，以及引导车辆使用预定走廊。

拥堵条件下，策略会从“机动性/绿波”转向 **排队管理**。若排队无法在绿灯内清空（周期失败），继续用未饱和条件下的偏移优化可能无效。

## Capacity drop

车道关闭、事故占道或显著降速会降低路段有效通行能力。需求若仍按原配时送入，排队会向上游传播并可能回溢进交叉口。信号响应应减少继续向已降容路段推入的流向绿灯，而不是假设“多放一点就能冲过去”。

临时限速主要改变到达节奏和饱和流率，不一定等价于封道。速度下降后，同一绿灯能通过的车辆数可能减少，上游到达更密，相邻路口协调需要按新的行程时间理解，而不是把该进口当成已中断。

## Upstream gating（入口限流）

当控制区内已过饱和时，FHWA 描述可通过显著减少进入拥挤干道的支路/入口绿灯，限制进入拥堵区的车辆数，以避免回溢封住上游交叉口、交叉口内部阻塞和转向车道溢出。这是 **metering / gating**，不是关闭路口。

项目中若只能输出 `target_phase`，gating 只能间接体现为：上游少选“送入受损方向”的相位。不得声称系统已有独立限流接口。

## Downstream spillback protection

拥堵配时调整包括：让下游路口更早放行可能阻挡直行的左转，降低左转溢出堵死直行的风险；调整相位以避免转向车道回溢；必要时保护下游存储，不要把车队推入已经没有空间的路段。下游占用已经很高时，出口路口继续加长绿灯可能恶化回溢。

## 指定流向优先与 traffic flush

对计划性活动、道路事件或疏散，主要目标是给指定流向优先、降低非经常性拥堵带来的总延误。一种操作是增加这些流向的绿灯，使信号对优先流向做 **flush**（冲放/清放）。同时可把交通吸引到预先指定的、延误更低的走廊。

Flush 不是“所有进口一起加绿”，也不是跳过黄灯/全红。优先流向的选择必须来自事件方向（事故受阻方向 vs 活动入口 vs 活动出口）。

## Coordinated corridor response

应先确定需要改配时的路线和交叉口，而不是平均对待区域所有路口。事故或高速阻断时，平行走廊可能成为分流路径，需沿该走廊一致地提高接收能力。只控制事件单路口、下游仍按反向高峰服务，会在第二、第三个路口形成新瓶颈并回灌。

## 计划性活动：ingress 与 egress 不同

FHWA Planned Special Events 指南强调：活动交通管理包含活动前规划、当日运行和活动后评估。开场与散场的需求方向相反。

- **Ingress / opening**：流量向场馆或活动区集中。控制重点是入口走廊的到达管理，避免入口交叉口被到达车队堵死，并限制与入场冲突的反向过饱和放行。
- **Egress / closing / evacuation corridor**：流量离开场馆。控制重点是疏散走廊的连续放行与下游接收，避免只加长出口绿灯。

不能把 opening 和 closing 写成同一套相位策略。

## Recovery timing 与终止条件

事件或抢占结束后，信号需要从特殊逻辑 **恢复** 到日常配时。手册指出恢复/过渡可能需要数十秒到数分钟；应避免在过渡期使用不安全的灯色跳变（例如黄灯后直接回绿灯而省略红灯清空，在抢占规范中是被禁止的一类跳变）。

交通工程上的退出条件通常是：优先走廊排队开始回落、周期失败减少、下游存储重新可用。事件时钟结束不等于排队立即消失，因此需要恢复控制时段。

## 与 CityPulse 的边界

上述原则用于生成高层 AI plan 的 **objective / protect / avoid / gating intent**。能否执行取决于项目 Protocol 2.0 与 SafePhaseController。黄灯、最小绿、全红清空不得由模型省略。

## 来源

1. FHWA Traffic Signal Timing Manual, Chapter 8 Signal Timing Maintenance: Operations and Monitoring
   - 发布机构：美国联邦公路管理局
   - 报告号：FHWA-HOP-08-024（已归档，后续由 Signal Timing Manual Second Edition 替代）
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop08024/chapter8.htm
   - 用于支持：拥堵配时转向排队管理、下游保护、入口 metering、事件与活动配时调整。
2. FHWA Traffic Signal Timing Manual, Chapter 9 Advanced Signal Timing Topics
   - 发布机构：美国联邦公路管理局
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop08024/chapter9.htm
   - 用于支持：计划性活动、事故与应急下的指定流向优先与 flush。
3. FHWA Managing Travel for Planned Special Events Handbook
   - 发布机构：美国联邦公路管理局
   - 报告号：FHWA-OP-04-010
   - 年份：2003
   - URL：https://ops.fhwa.dot.gov/publications/fhwaop04010/
   - 用于支持：计划性活动需分阶段管理，开场与散场需求不同。
4. FHWA Reducing Non-Recurring Congestion
   - 发布机构：美国联邦公路管理局
   - URL：https://ops.fhwa.dot.gov/program_areas/reduce-non-cong.htm
   - 用于支持：事故、施工、活动等非经常性拥堵类别。
