# demo_1 官方信号配置

## 文件放置

将建议包中的文件放到项目内相同的相对路径。`official_tls_plans.json` 是完整替换文件，其余不存在的文件直接新增。

在项目 `.gitignore` 末尾追加：

```gitignore
# Generated SUMO signal artifacts
data/maps/sumo/generated/
*.bak
```

已提交的两个 `TotalMap_20.net.xml.*.bak` 和两个 `TotalMap_20.intersections.json.*.bak` 可在确认不再需要后从 Git 中删除；不要删除基础 `TotalMap_20.net.xml`。

## 服务器构建

```bash
export SUMO_HOME=/path/to/sumo
export PATH="$SUMO_HOME/bin:$PATH"

cd /path/to/citypulse-v2x-sim
python -m simulation.sumo.build_tls --intersections demo_1
```

构建输出全部位于 `data/maps/sumo/generated/`：

- `TotalMap_20.signals.net.xml`：加入 demo_1 TLS 的派生路网。
- `official_tls.add.xml`：三套官方 static program。
- `tls_manifest.json`：算法运行时使用的官方路口到物理 TLS 映射。
- `official_tls_connections.csv`：人工核对每个 linkIndex 的进口和转向。
- `official_tls_validation.rou.xml`：当前已选路口的确定性验证车流。
- `official_tls.sumocfg`：可直接运行的 SUMO 配置。

构建器不会修改 `TotalMap_20.net.xml`。任何 edge、junction、受控连接、周期或冲突校验失败都会以非零状态退出。

## 固定配时验收

```bash
python -m simulation.sumo.run \
  --gui \
  --realtime \
  --mode fixed \
  --intersection demo_1 \
  --program demo_1_morning_peak
```

另外两套方案分别是 `demo_1_off_peak` 和 `demo_1_evening_peak`。每次实验显式选择方案，表格中的时间范围仅作为元数据，不自动按仿真时钟切换。

在 SUMO GUI 中对照 `official_tls_connections.csv` 检查：

1. 相位 1 只保护东西向直行。
2. 相位 2 只保护东西向左转和掉头。
3. 相位 3 只保护南北向直行。
4. 相位 4 只保护南北向左转和掉头。
5. 右转始终使用让行绿 `g`。
6. 每次切换都经过 3 秒黄灯和 2 秒清空。

## 算法接入

算法类只需实现 `simulation.sumo.policy.SignalPolicy` 的三个方法，并返回官方相位号：

```python
class MyPolicy:
    def reset(self, metadata):
        self.metadata = metadata

    def act(self, observation):
        return {"demo_1": 3}

    def close(self):
        pass
```

运行示例策略：

```bash
python -m simulation.sumo.run \
  --mode policy \
  --intersection demo_1 \
  --program demo_1_morning_peak \
  --policy algorithms.baseline.longest_queue_first.policy:LongestQueuePolicy
```

runner 独占 TraCI。算法看见的是 `demo_1`、官方相位 1-4 和进口 lane 指标，不接触 SUMO `tls_id`、`linkIndex` 或灯色字符串。

## 测试

不安装 SUMO 也可以执行配置与状态机测试：

```bash
python -m unittest discover -s tests -p "test_signal_*.py" -v
```

服务器完成构建后，再分别运行三套固定配时至少一个完整周期，并检查 SUMO 日志中不存在网络加载、冲突、碰撞或 teleport 错误。

