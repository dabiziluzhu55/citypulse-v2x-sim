# 环境安装

## 依赖

- SUMO（需设置 `SUMO_HOME`）
- CARLA 0.9.16（联合仿真需设置 `CARLA_ROOT`）
- Python 3.10
- NumPy 1.23+ 和 SciPy 1.9+（routeSampler 全局优化）

## 环境变量

```bash
export SUMO_HOME=/path/to/sumo
export CARLA_ROOT=/path/to/CARLA_0.9.16
```

## Python 依赖

```bash
pip install -r requirements.txt
```

正式车流构建还会检查 `$SUMO_HOME/tools/routeSampler.py`、`duarouter`、`netconvert`
和 `sumo`。开发机没有 SUMO 时可运行 `python -m simulation.sumo.build_tls --validate-only`
完成只读数据与路网预检。
