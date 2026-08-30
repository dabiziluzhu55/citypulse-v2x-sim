# 可复现仿真案例库

本目录用于第二阶段沉淀经过验证的仿真实验，不收录未经复现的“成功案例”。每个案例建议一个 Markdown 文件，并把原始 JSON/TripInfo/会话 manifest 保留在知识库外的稳定制品位置，通过哈希或相对链接引用。

## 案例最小模板

```markdown
# 案例标题

## 问题与假设
## 代码基线与环境
## preset / period / 窗口 / seed
## 交通需求与扰动
## 对照算法与模型别名
## 指标结果（含 metric_sources / warnings）
## 空间与时序证据
## 结论及适用边界
## 复现命令
## 制品与 SHA-256
## 来源
```

## 纳入条件

案例必须可复现、至少有一个对照算法、固定场景条件并保留完整警告。强化学习结论应使用多个种子并报告失败运行；局部预设需标明是否零样本。不得把单次演示、历史旧指标口径或手工截图写成普遍结论。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_eval/README.md; traffic_eval/eval_cli.py; docs/official20_prediction_handoff.md
   - 用于支持：评估入口、复现条件和模型制品契约。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
2. FHWA Traffic Analysis Tools Toolbox
   - 发布机构：美国联邦公路管理局
   - 年份：持续更新
   - URL：https://ops.fhwa.dot.gov/trafficanalysistools/toolbox.htm
   - 用于支持：微观仿真校准、分析和效果指标应可追溯。
