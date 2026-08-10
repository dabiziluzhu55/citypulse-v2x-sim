# 管控算法

正式算法由算法组维护，当前确定的控制方式为：

- 官方固定配时；
- Max Pressure；
- IPPO 强化学习；
- **CoSLight 多路口强化学习 + 规则型车辆引导**；
- **分层 CTDE 车道引导（HCG-MAPPO，规划中）**。

固定配时直接由仿真端执行。其他三类算法使用相同的本地 Python 协议 2.0，不直接访问
SUMO 或 TraCI；协议同时提供路口聚合状态、单车运动/油耗状态，并接收信号相位、目标
速度和当前道路换道动作。

算法组只需阅读 [algorithm_interface.md](algorithm_interface.md)。

---

## 车-路协同路线图

| 阶段 | 信号控制 | 车辆引导 | 状态 |
|------|---------|---------|:---:|
| V1 | CoSLight MAPPO | 规则型（权限过滤 + reject-fix + 静止门控） | ✅ 已完成 |
| V2 | CoSLight（固定） | Lane Actor IPPO 参数共享（进口道路层） | 🔨 开发中 |
| V3 | CoSLight + 信号意图通信 | Lane Actor + CTDE 集中式 Critic | 📋 规划中 |

### V1 车辆引导模块

位于 `algorithms/coslight/controller.py::_build_vehicle_actions()`，纯规则决策：

1. **候选车道**：同 edge 排队最短的车道
2. **权限过滤**：通过 `edge_lanes[*].allowed_vehicle_type_ids` 排除禁行车道
3. **reject-fix**：上次同车道推荐被拒（`lane_change_status == "not_completed"`）则跳过
4. **静止门控**：速度 < 0.5 m/s 或内部 edge → 不推荐换道
5. **速度推荐**：绿灯滑行 / 红灯减速

### V2/V3 Lane Actor 状态构建

位于 `algorithms/coslight/lane_state.py`：

- **41 维状态**：车辆自身(8) + 三车道上下文(18) + 信号意图(7) + 区域(3) + 历史(5)
- **进口道路槽位**：每条受控进口道一个固定决策槽位，从 signal metadata 的 `incoming_lanes` 提取
- **动作 mask**：KEEP/LEFT/RIGHT，含权限、路线、速度、换道状态多重过滤
- **集中式 Critic**（规划中）：两级 masked pooling（道路→路口→区域）
