# demo_2 官方信号配置

## 路口拓扑

官方路口 `demo_2` 对应 SUMO junction `317`，是三岔口：

| 进口 | SUMO edge | 作用 |
|---|---|---|
| 东北主路 | `-56734` | 主路驶向西南 |
| 西南主路 | `-57228` | 主路驶向东北 |
| 东南支路 | `-51425` | 支路驶入主路 |

相位 1“南北向直行”保护东北、西南主路双向直行。东北主路进入东南支路的左转使用让行绿 `g`，向对向直行车辆让行。

相位 2“西南向左转”保护东南支路驶向西南主路的左转。右转继续使用常让行绿 `g`；SUMO 自动生成的掉头连接不属于官方方案，始终保持红灯。

## 构建

demo_1 的几何冲突不会影响单独构建 demo_2：

```bash
export SUMO_HOME=/path/to/sumo
export PATH="$SUMO_HOME/bin:$PATH"

python -m simulation.sumo.build_tls --intersections demo_2
```

成功后，`data/maps/sumo/generated/` 应包含：

- `TotalMap_20.signals.net.xml`
- `official_tls.add.xml`
- `tls_manifest.json`
- `official_tls_connections.csv`
- `official_tls_validation.rou.xml`
- `official_tls.sumocfg`

构建器会自动从派生路网中删除 `value=""` 的空 `<param>`。这些空参数会让部分
SUMO 版本在 `NLHandler::addParam` 中触发断言，因此不要跳过构建器而直接把基础
`TotalMap_20.net.xml` 交给 `sumo-gui`。

## 固定配时验收

早高峰方案：

```bash
python -m simulation.sumo.run \
  --gui \
  --realtime \
  --mode fixed \
  --intersection demo_2 \
  --program demo_2_morning_peak
```

另外两套方案是 `demo_2_off_peak` 和 `demo_2_evening_peak`，周期均为 80 秒。

验收时确认：

1. 相位 1 的主路双向直行为大写 `G`。
2. 相位 1 的东北主路左转为小写 `g`，不会抢占对向直行路权。
3. 相位 2 只保护东南支路驶向西南的左转。
4. 每个相位绿灯后有 3 秒黄灯，不插入全红阶段。
5. 右转始终为让行绿，掉头始终为红灯。
