# 项目场景定义

## 场景预设

| preset_id | 代码标签 | 受控路口 | map_template |
| --- | --- | --- | --- |
| `xiongan_20` | 雄安20路口路网 | `demo_1`–`demo_20` | `xiongan20` |
| `east_dense` | 东部密集路口场景 | `demo_3/5/6/9` | `east_dense` |
| `west_dense` | 西部密集路口场景 | `demo_14/15/19` | `west_dense` |

场景标签和 `demo_*` 是项目内部标识，不是公开地理路口名称。当前代码没有把东部标为校园。西部预设三个路口的项目映射坐标彼此较近，但这只能支撑“密集路口”的空间特征，不能单独证明道路红线宽度、沿路间距或存储能力；这些几何与业务语义仍需由 SUMO 拓扑、地图标注、赛题资料或现场数据补充。

## 雄安规划知识与仿真事实

雄安官方规划明确提出起步区 10–15 公里/平方公里的路网密度，雄东片区控详规给出约 12 公里/平方公里并区分主干路、次干路和支路。这些是地域规划依据，可用于设计和校准仿真，但不能直接写成 `xiongan_20` 已达到的测量结论。项目正式对外报告前，应在相同裁剪边界内计算道路中心线总长度/面积、路段长度分布、节点密度、车道与道路等级，再与对应片区口径比较。详细规则见 `04_scenarios/xiongan_narrow_road_dense_network.md`。

## 时段与窗口

period 只能是 `morning_peak`、`off_peak`、`evening_peak`。场景编译还接收窗口起点、持续时间、随机种子、步长、来源车道和流量倍率；倍率允许范围由代码校验。比较算法必须固定这些条件。

## 扰动事件

可注入 `lane_closure`、`speed_limit`、`accident`、`major_event_opening`、`major_event_closing`。Backend 确保目标路口属于预设、车道属于当前场景、事件 ID 不重复且时间/位置合法。异常停车和普通拥堵不是独立注入类型；拥堵通常由需求、控制和阻塞共同演化。

## 场景导出边界

`xiongan_20` 可额外导出全局九区域 OD/TAZ；`east_dense` 和 `west_dense` 只导出局部场景 SUMO 包，不包含该全局 OD，因为其空间口径不匹配。知识库不得把全局 OD 统计嫁接到局部预设。

## 预测适用性

STGCN 模型固定 20 路口节点顺序。局部预设的智能输出应检查运行时代码如何填充未选路口以及 fallback 状态；本版不把官方 20 路口预测精度直接外推为东/西局部场景精度。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/scenario/presets.py; backend/app/scenario/resolver.py; simulation/sumo/scenario.py; backend/app/schemas/events.py; backend/app/services/scenario_export_service.py; docs/official20_prediction_handoff.md
   - 用于支持：预设、period、事件校验、OD 和预测边界。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
2. 《河北雄安新区规划纲要》
   - 发布机构：中共中央、国务院批复；中国雄安官网公开
   - 年份：2018
   - URL：https://www.xiongan.gov.cn/2018-04/21/c_129855813_8.htm
   - 用于支持：起步区路网密度及城市街道分层规划背景。
3. 《河北雄安新区雄东片区控制性详细规划》
   - 发布机构：河北雄安新区管理委员会
   - 版本：2020 年 4 月
   - URL：https://www.xiongan.gov.cn/download/xaxqxdpqkzxxxgh.pdf
   - 用于支持：片区路网密度、道路分级以及规划指标不可跨空间范围套用。
