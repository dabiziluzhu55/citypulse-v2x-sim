#!/usr/bin/env python3
"""
toolchain_env.py — 工具链统一 SUMO/CARLA 环境路径解析

供 validate.py / run_xml2odr.py(xml2odr.cli) / run_cosimulation.py /
spectator_coords.py 共用。配置来源与优先级(高 → 低):
    1. CLI 参数(各脚本自行处理,优先于本模块)
    2. 环境变量(SUMO_HOME / CARLA_ROOT)
    3. config/toolchain.json(本目录下,脚本相对路径,不受 cwd 影响)
    4. PATH 查找(netconvert 二进制)

**不做任何硬编码路径猜测**——SUMO/CARLA 安装位置只来自环境变量或
config/toolchain.json;未配置时明确报错并提示配置方式。

toolchain.json 模板(空值 = 不配置,回落到环境变量):
    {
      "sumo_home": "",        // SUMO 安装目录(如 /opt/sumo)
      "carla_root": "",       // CARLA 源码目录(如 .../carla-0.9.16-src)
      "sumo_toolkit_dir": ""  // 官方联仿工具包目录(含 util/data/opendrive_netconvert.typ.xml,
                              //   如 <carla_root>/Co-Simulation/Sumo)
    }
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys

# config/toolchain.json 与本模块同目录(脚本相对,避免受 cwd 影响)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                           "config", "toolchain.json")

# 官方联仿工具包中 typ.xml 的相对位置(与 CARLA netconvert_carla.py 的 basedir 约定一致)
_TYP_REL = os.path.join("util", "data", "opendrive_netconvert.typ.xml")

_config: dict | None = None


def load_config() -> dict:
    """读取 toolchain.json(容错:缺文件 / 坏 JSON → 空 dict)。"""
    global _config
    if _config is not None:
        return _config
    _config = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _config = data
    except (OSError, json.JSONDecodeError):
        pass
    return _config


def get(key: str) -> str:
    """读取配置值(空字符串视为未设置)。"""
    v = load_config().get(key)
    return v if isinstance(v, str) else ""


def resolve_sumo_home() -> str:
    """SUMO 安装目录:环境变量 SUMO_HOME > config sumo_home > ""。"""
    env = os.environ.get("SUMO_HOME", "")
    if env:
        return env
    return get("sumo_home")


def resolve_carla_root() -> str:
    """CARLA 源码目录:环境变量 CARLA_ROOT > config carla_root > ""。"""
    env = os.environ.get("CARLA_ROOT", "")
    if env:
        return env
    return get("carla_root")


def add_carla_pythonapi_to_path() -> None:
    """把 CARLA Python API egg 加入 sys.path,使 ``import carla`` 无需手动设
    PYTHONPATH 即可工作。CARLA 源码目录只来自 ``resolve_carla_root()``
    (环境变量 CARLA_ROOT > config/toolchain.json carla_root,不做硬编码猜测)。"""
    root = resolve_carla_root()
    if not root or not os.path.isdir(root):
        return
    dist_dir = os.path.join(root, "PythonAPI", "carla", "dist")
    if os.path.isdir(dist_dir):
        for egg in sorted(glob.glob(os.path.join(dist_dir, "*.egg"))):
            if egg not in sys.path:
                sys.path.insert(0, egg)


def find_netconvert() -> str | None:
    """netconvert 可执行文件:shutil.which > $SUMO_HOME/bin/netconvert。"""
    nc = shutil.which("netconvert")
    if nc:
        return nc
    sumo_home = resolve_sumo_home()
    if sumo_home:
        cand = os.path.join(sumo_home, "bin", "netconvert")
        if os.path.isfile(cand):
            return cand
    return None


def sumo_tools_dir() -> str | None:
    """SUMO tools 目录($SUMO_HOME/tools,供 sys.path 加 sumolib/traci)。"""
    sumo_home = resolve_sumo_home()
    if not sumo_home:
        return None
    tools = os.path.join(sumo_home, "tools")
    return tools if os.path.isdir(tools) else None


def resolve_path_dir(key: str, cli_value: str = "", default: str = "") -> str:
    """工具链目录解析(优先级:CLI 参数 > toolchain.json[key] > 默认值)。

    相对路径以本模块所在目录(XML2Odr/)为基准 realpath 解析
    (如 "../../data/exports" → XML2Odr 上溯两级后的 data/exports);绝对路径原样使用。
    """
    v = cli_value or get(key) or default
    if not v:
        return ""
    if os.path.isabs(v):
        return os.path.realpath(v)
    script_dir = os.path.dirname(os.path.realpath(__file__))
    return os.path.realpath(os.path.join(script_dir, v))


def resolve_exports_dir(cli_value: str = "") -> str:
    """CARLA 导出数据根目录(默认 ../../data/exports)。"""
    return resolve_path_dir("exports_dir", cli_value, os.path.join("..", "..", "data", "exports"))


def resolve_maps_xodr_dir(cli_value: str = "") -> str:
    """xml2odr 产物目录(xodr + clipped net,默认 ../../data/maps/xodr)。"""
    return resolve_path_dir("maps_xodr_dir", cli_value,
                            os.path.join("..", "..", "data", "maps", "xodr"))


def resolve_maps_carla_dir(cli_value: str = "") -> str:
    """CARLA 地图目录(RoadRunner 导出,默认 ../../data/maps/carla)。"""
    return resolve_path_dir("maps_carla_dir", cli_value,
                            os.path.join("..", "..", "data", "maps", "carla"))


def resolve_totalmap_net(cli_value: str = "") -> str:
    """SUMO 源路网 TotalMap 路径(默认 ../../data/maps/sumo/generated/network/
    TotalMap_20.signals.net.xml)。run_xml2odr / list_junctions / tp /
    plan_lidar_points 共用。"""
    return resolve_path_dir("totalmap_net", cli_value,
                            os.path.join("..", "..", "data", "maps", "sumo",
                                         "generated", "network",
                                         "TotalMap_20.signals.net.xml"))


def find_typ_file() -> tuple[str | None, list[str]]:
    """官方 typ.xml(opendrive_netconvert.typ.xml)解析。

    候选顺序(无硬编码路径,未配置的来源以说明文字占位展示):
      1. config sumo_toolkit_dir(用户显式指定)
      2. CARLA 源码 CARLA_ROOT/Co-Simulation/Sumo(由环境变量/config 推导)
    返回 (命中路径 | None, 全部候选列表)——失败时候选列表供报错提示。
    """
    # 候选列表恒为 2 项(未配置的来源以说明文字占位),失败时全部展示给用户
    candidates: list[str] = []

    toolkit = get("sumo_toolkit_dir")
    candidates.append(os.path.join(toolkit, _TYP_REL) if toolkit
                      else "(未配置 sumo_toolkit_dir)")

    carla_root = resolve_carla_root()
    candidates.append(os.path.join(carla_root, "Co-Simulation", "Sumo", _TYP_REL)
                      if carla_root else "(未配置 CARLA_ROOT / carla_root)")

    for cand in candidates:
        if cand.startswith("("):
            continue
        real = os.path.realpath(cand)
        if os.path.isfile(real):
            return real, candidates
    return None, candidates
