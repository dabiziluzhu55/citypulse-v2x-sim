# 车道阻塞与排队回溢

## 定义

车道阻塞是车道可用能力因关闭、障碍、事故、故障车或其他限制而下降。排队回溢是下游队列延伸并占据上游路段或路口的状态。两者可同时发生，但回溢也可能纯由需求过大或信号失配形成。

## 可能观察到的数据特征

物理阻塞候选：放绿且越过启动损失后，车道仍低速、停止和等待不释放；同向相邻车道表现不同；允许速度被限制或事件记录显示关闭。回溢候选：本车道与下游车道同时高停止/高占用，下游先恶化，上游随后持续排队。

“受阻车道变空”也可能发生，因为车辆改走同向其他车道；因此项目旧的空车道关闭启发式默认关闭。单纯空车道不能证明封闭。

## 可能造成的影响

阻塞降低有效容量，诱发换道与局部冲突；回溢可能封住其他 movement、影响上游信号并扩散至路网。短路段密网中，单路口追求放行量可能加重下游堵塞。

## 可考虑的管控动作

先区分局部物理阻塞和下游回溢。物理阻塞需要外部确认与处置，信号只能缓解；回溢可减少向饱和下游放行、保护横向或替代 movement，并重点比较 Max Pressure 或通过验证的协调模型。动作不得牺牲相位安全转换。

## 当前项目能力

Backend 支持 `lane_closure` 注入，可指定车道和时窗；规则模块可产生 `lane_blocked` 与 `spillback` 候选语义，并支持 CUSUM 持续性判别。启发式阈值属于实验配置，不应推广为现实世界统一阈值。

## 来源

1. Recurring Traffic Bottlenecks: Appendix B
   - 发布机构：FHWA Office of Operations
   - 年份：2018
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop18013/appb.htm
   - 用于支持：容量损失、事件瓶颈和排队消散机制。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; simulation/sumo/events.py; algorithms/event_detection/rules.py
   - 用于支持：车道关闭注入、回溢检测和阈值边界。

