# 环境安装

## 依赖

- SUMO 及其 Python `libsumo` binding（需设置 `SUMO_HOME`）
- CARLA 0.9.16（联合仿真需设置 `CARLA_ROOT`）
- Python 3.10
- NumPy 1.23+ 和 SciPy 1.9+（routeSampler 全局优化）

## 环境变量

```bash
export SUMO_HOME=/path/to/sumo
export CARLA_ROOT=/path/to/CARLA_0.9.16
```

生产 headless 会话严格使用 libsumo，不会在 binding 缺失时回退 TraCI。部署前验证：

```bash
python -c "import libsumo; import sumolib"
```

只有运行本地 `--gui` 调试的机器还需要验证：

```bash
python -c "import traci"
```

## Python 依赖

```bash
pip install -r requirements.txt
```

正式车流构建还会检查 `$SUMO_HOME/tools/routeSampler.py`、`duarouter`、`netconvert`
和 `sumo`。部分 Linux 发行版需要单独安装 `python3-libsumo`，也可使用与服务器 SUMO
版本一致的官方 `libsumo` Python 包。开发机没有 SUMO 时可运行
`python -m simulation.sumo.build_tls --validate-only`
完成只读数据与路网预检。

较旧的 SUMO `routeSampler.py` 可能没有 `--no-sampling`。构建器会自动探测：新版会
显式传入该参数；旧版会保留 `--optimize full` 并省略该参数，同时继续执行全部独立
质量校验。缺少其他优化、输出或 mismatch 参数时仍会在构建前失败。

SUMO 1.12 等旧版还能优化带 `via` 的多边计数关系，但其 native mismatch XML 写出器
会在这类关系上崩溃。构建器会只对受影响的计数文件省略 `--mismatch-output`，并生成
带 `native="false"` 标记的兼容 XML；PCU、GEH、零流量和跨路口校验仍由
`traffic_quality_PERIOD.json/csv` 完整提供。新版继续保留 routeSampler 原生 mismatch。

车型长短途分配和驻网负载目标由
`data/maps/sumo/traffic_generation_policy.json` 独立配置，`build_tls` 默认自动加载。需要验证
其他策略文件时可传入 `--traffic-policy PATH`。该文件不替代官方需求：官方 PCU、GEH、零流量
和路口总量仍是构建硬门槛；距离比例、最终车型比例和负载目标采用最优结果加告警的策略。
