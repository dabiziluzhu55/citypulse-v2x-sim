# 指标综合解释与比较规则

## 先确认可比性

只有预设、period、窗口起点、仿真时长、随机种子、交通需求、车辆组成、扰动和 SUMO 版本一致时，算法指标才可直接比较。强化学习模型还需记录 checkpoint/model alias；否则差异可能来自输入而非控制策略。

## 先看质量字段

每次结论前检查 `metric_sources` 和 `warnings`。运行中行程、等待和燃油可能是 provisional；终态应由 TripInfo 回填。缺失值表示不可用，禁止改为 0。历史评估文件可能采用旧来源名称或旧燃油单位，不能与当前口径无条件合并。

## 多指标判断

- 拥堵研判：实时车道速度、停止数、等待和占用率 + 正式平均排队、通行量和完成率。
- 方案效率：平均行程、平均等待、排队、通行量、完成率。
- 运行代价：决策延迟和燃油强度。
- 安全筛查：急刹事件/率；仅作替代指标。

某算法若降低等待却增加回溢、急刹或燃油强度，应报告权衡，不给“全面更优”的结论。小时通行量是有限窗口到达数的外推，短窗口中易受尚未到达车辆影响，应与完成率和行程时间一起看。

## 多次运行

单一随机种子只能描述一次仿真。建议相同场景运行多个种子，报告均值、离散程度、成功/失败次数和每项可用样本数。只有差异稳定且实际意义明确时，才推荐替换基线。

## 算法对比输出模板

无 AI 的公平对比应包含：比较条件、可用/缺失指标、主要改善、主要退化、事件与空间证据、结论置信度。不要让 CityPulse-Qwen 根据指标去改选 `control_mode`。

**【规划功能】** 含 AI 接管的运行应额外报告 AI scope、window、fallback 次数和恢复 baseline 的时刻，并与纯 baseline 实验分开存放。

## 来源

1. FHWA Traffic Analysis Tools Toolbox
   - 发布机构：美国联邦公路管理局
   - 年份：持续更新
   - URL：https://ops.fhwa.dot.gov/trafficanalysistools/toolbox.htm
   - 用于支持：微观仿真应用、校准和多类效果指标框架。
2. FHWA Guide on Consistent Application of Traffic Analysis Tools
   - 发布机构：美国联邦公路管理局
   - 年份：2011
   - URL：https://www.fhwa.dot.gov/publications/research/operations/11064/004.cfm
   - 用于支持：通常需要多项 MOE 共同判断目标。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_eval/collector.py; traffic_eval/tripinfo.py; traffic_eval/eval_cli.py
   - 用于支持：metric_sources、warnings、临时/终态和算法比较入口。
