# 算法目录

正式算法由算法组独立维护，包括 Max Pressure、IPPO 和多路口强化学习算法。

算法代码不需要导入 SUMO 或 TraCI。跨进程部署时实现
[`docs/algorithm_interface.md`](../docs/algorithm_interface.md) 中的三个 HTTP 接口；同机训练时可复制
[`local_policy_example.py`](local_policy_example.py)，实现同名的 `initialize/step/finish` Python 函数。
两种方式收发完全相同的字典结构，本地方式不经过网络和 JSON 编解码。
固定配时由仿真端直接执行，不经过算法服务。当前接口为不兼容旧版的协议 2.0，算法响应
必须同时提供 `actions.signals` 和 `actions.vehicles` 两个对象。

只消费状态、不返回控制动作的 AI 训练入口见
[`ai_observer_example.py`](ai_observer_example.py) 和
[`docs/local_transport_ai_observer.md`](../docs/local_transport_ai_observer.md)。
