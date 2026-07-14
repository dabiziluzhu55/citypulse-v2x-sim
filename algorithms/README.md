# 算法目录

正式算法由算法组独立维护，包括 Max Pressure、IPPO 和多路口强化学习算法。

算法代码不需要导入 SUMO、TraCI 或本仓库内部模块，只需实现
[`docs/algorithm_interface.md`](../docs/algorithm_interface.md) 中的三个 HTTP 接口。
固定配时由仿真端直接执行，不经过算法服务。
