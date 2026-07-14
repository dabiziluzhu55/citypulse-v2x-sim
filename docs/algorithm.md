# 管控算法

正式算法由算法组维护，当前确定的控制方式为：

- 官方固定配时；
- Max Pressure；
- IPPO 强化学习；
- 多路口强化学习。

固定配时直接由仿真端执行。其他三类算法使用相同的 HTTP/JSON 协议，不直接访问
SUMO 或 TraCI。

算法组只需阅读 [algorithm_interface.md](algorithm_interface.md)。
