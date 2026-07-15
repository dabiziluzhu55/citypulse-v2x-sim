# 管控算法

正式算法由算法组维护，当前确定的控制方式为：

- 官方固定配时；
- Max Pressure；
- IPPO 强化学习；
- 多路口强化学习。

固定配时直接由仿真端执行。其他三类算法使用相同的 HTTP/JSON 协议 2.0，不直接访问
SUMO 或 TraCI；协议同时提供路口聚合状态、单车运动/油耗状态，并接收信号相位、目标
速度和当前道路换道动作。

算法组只需阅读 [algorithm_interface.md](algorithm_interface.md)。
