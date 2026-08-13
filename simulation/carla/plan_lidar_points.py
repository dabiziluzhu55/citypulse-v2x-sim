#!/usr/bin/env python3
"""
plan_lidar_points.py — 路侧 Lidar 自动布点(每路口中心一台大范围 lidar)

按 TAZ(config/taz.json)指定的路口组,在每个**路口中心正上方**安放一台
360° 大范围 lidar(--range 默认 50m,覆盖"路口 + 每条路向外延伸 30m"的
整片区域,不需要严丝合缝),写入 config/export_configs/<map>.json — 与
相机条目共用同一个 sensors[] 数组,保留现有条目,同路口前缀
(demo_N_lidar*)的旧 lidar 条目就地清理替换。

单台/路口是路侧采集的推荐形态:多台 lidar 并行时 CARLA 每台都做独立的
ray cast 计算,fps 会骤降至个位数。纯离线(无 CARLA),仅 stdlib。

用法:
    python plan_lidar_points.py --taz WestZone --dry-run --print-coverage
    python plan_lidar_points.py --taz WestZone
    python plan_lidar_points.py --taz WestZone --range 60 --z 8
"""

from __future__ import annotations

import argparse
import json
import os
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import toolchain_env  # 统一环境/路径配置(源路网默认 totalmap_net)
from spectator_coords import _LIDAR_RANGES  # lidar 参数范围校验(与手动布点一致)
from xml2odr.batch_clip import (load_taz_config,  # TAZ 组解析(与 run_xml2odr 同源)
                                _build_name_to_junction_id)

# lidar 条目默认参数(与 config/export.example.json 的 lidar 示例一致;
# range=50 覆盖"路口半宽 ~15m + 每条路延伸 30m")
_DEFAULT_LIDAR_PARAMS: Dict[str, Any] = {
    "channels": 64,
    "range": 50.0,
    "points_per_second": 1_000_000,
    "rotation_frequency": 20,
    "upper_fov": 10.0,
    "lower_fov": -30.0,
}

JunctionRec = Dict[str, Any]    # {"x", "y", "jtype"}


def scan_net(path: str) -> Dict[str, JunctionRec]:
    """流式扫描 net.xml,只收集布点需要的元素:``<junction id jtype x y>``
    (360° lidar 放在路口中心,不需要道路几何)。处理完即 ``clear()``,
    44MB 文件秒级。"""
    junctions: Dict[str, JunctionRec] = {}
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "junction":
            junctions[elem.get("id")] = {
                "x": float(elem.get("x", 0.0)),
                "y": float(elem.get("y", 0.0)),
                "jtype": elem.get("type", ""),
            }
        elem.clear()
    return junctions


def plan_for_junctions(junctions: Dict[str, JunctionRec],
                       intersections: List[Tuple[str, str]],
                       z: float = 6.0, pitch: float = 0.0,
                       lidar_params: Optional[Dict[str, Any]] = None,
                       ) -> List[Dict[str, Any]]:
    """每个路口中心生成一台 360° lidar 条目。

    ``intersections`` 为 [(demo 名, junction_id), ...];条目名
    ``f"{demo}_lidar"``;点位 = 路口中心正上方(CARLA 系 y 取反);yaw 固定 0
    (360° 扫描,点云在传感器局部系,与朝向无关)。坐标全精度 float。
    """
    params = dict(_DEFAULT_LIDAR_PARAMS if lidar_params is None else lidar_params)
    entries: List[Dict[str, Any]] = []
    for demo, jid in intersections:
        jrec = junctions.get(jid)
        if jrec is None:
            print(f"  [warn] junction '{jid}' (demo_{demo}) not found in net")
            continue
        entries.append({
            "name": f"{demo}_lidar",
            "type": "lidar",
            "transform": {
                "x": jrec["x"], "y": -jrec["y"], "z": z,
                "pitch": pitch, "yaw": 0.0, "roll": 0.0,
            },
            **params,
        })
    return entries


def merge_lidar_entries(path: str, map_name: str,
                        entries: List[Dict[str, Any]]) -> int:
    """把 lidar 条目合并进 ``path`` 对应的导出配置。

    保留顶层键(version/map/saved_at 等)与**非 lidar 条目**(相机等);
    **按路口前缀清理旧条目**: 对每个新条目名 ``demo_N_lidar``,删除现有
    配置中同名或以 ``demo_N_lidar_`` 开头的旧条目(兼容第一版 per-road
    多台残留),再写入新的单台条目;刷新 saved_at;原子写
    (tmp + os.replace,与 spectator_coords._upsert_sensor_entry 同机制)。

    Returns:
        被替换/清理的条目数。
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError(f"{path} is not a JSON object")
    else:
        doc = {"version": 1}
    doc.setdefault("map", map_name)
    doc["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    sensors = doc.setdefault("sensors", [])
    if not isinstance(sensors, list):
        raise ValueError(f"{path}: 'sensors' is not a list")

    prefixes = sorted({e["name"] for e in entries})
    existing = [s for s in sensors if isinstance(s, dict)]
    stale = sum(1 for s in existing
                if any(s.get("name") == p or
                       s.get("name", "").startswith(p + "_")
                       for p in prefixes))
    doc["sensors"] = (
        [s for s in sensors if not (
            isinstance(s, dict) and any(
                s.get("name") == p or s.get("name", "").startswith(p + "_")
                for p in prefixes))]
        + entries)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return stale


def print_coverage(junctions: Dict[str, JunctionRec],
                   intersections: List[Tuple[str, str]],
                   z: float, range_m: float) -> None:
    """打印每个路口的中心坐标与布点位(路口中心正上方)及覆盖半径。"""
    for demo, jid in intersections:
        jrec = junctions.get(jid)
        if jrec is None:
            continue
        print(f"\n{demo} (junction {jid}): SUMO center "
              f"({jrec['x']:.2f}, {jrec['y']:.2f}) → CARLA "
              f"({jrec['x']:.2f}, {-jrec['y']:.2f})")
        print(f"  lidar '{demo}_lidar' at z={z:g} m, "
              f"coverage radius {range_m:g} m (路口+每条路延伸 ~30m)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _check_param(value: float, key: str, lo: float, hi: float) -> float:
    if not (lo <= value <= hi):
        raise argparse.ArgumentTypeError(
            f"{key}: out of range [{lo:g}, {hi:g}], got {value:g}")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="按 TAZ 为路口组生成路侧 lidar 点位(每路口中心一台)并写入导出配置")
    parser.add_argument("--taz", required=True,
                        help="config/taz.json 中的 TAZ 组名,如 WestZone")
    parser.add_argument("--map", default=None,
                        help="地图名(默认取 TAZ 组名),决定默认 --out")
    parser.add_argument("--net", default="",
                        help="SUMO 路网(默认 config/toolchain.json totalmap_net,"
                             "即 ../../data/maps/sumo/generated/network/"
                             "TotalMap_20.signals.net.xml)")
    parser.add_argument("--intersections", default="config/intersections.json",
                        help="demo 名 → junction_id 映射")
    parser.add_argument("--taz-config", default="config/taz.json",
                        help="TAZ 组配置")
    parser.add_argument("--out", default=None,
                        help="输出导出配置路径(默认 config/export_configs/<map>.json)")
    parser.add_argument("--z", type=float, default=6.0, help="传感器高度(米)")
    parser.add_argument("--pitch", type=float, default=0.0,
                        help="传感器俯仰角(度,负值下倾)")
    for key, (lo, hi) in _LIDAR_RANGES.items():
        parser.add_argument(f"--{key.replace('_', '-')}", type=float,
                            default=_DEFAULT_LIDAR_PARAMS[key],
                            help=f"{key} [{lo:g}, {hi:g}]")
    parser.add_argument("--print-coverage", action="store_true",
                        help="打印每个路口的布点位与覆盖半径(不写文件)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将写入的条目,不写文件")
    args = parser.parse_args(argv)
    args.net = args.net or toolchain_env.resolve_totalmap_net("")

    map_name = args.map or args.taz
    out = args.out or os.path.join("config", "export_configs", map_name + ".json")

    lidar_params: Dict[str, Any] = {}
    for key, (lo, hi) in _LIDAR_RANGES.items():
        value = getattr(args, key)
        _check_param(value, key, lo, hi)
        lidar_params[key] = value

    print(f"scanning net: {args.net} ...")
    junctions = scan_net(args.net)

    taz_cfg = load_taz_config(args.taz_config)
    group = next((g for g in taz_cfg["taz_groups"] if g["name"] == args.taz), None)
    if group is None:
        names = ", ".join(g["name"] for g in taz_cfg["taz_groups"])
        parser.error(f"TAZ group '{args.taz}' not found (available: {names})")

    with open(args.intersections, "r", encoding="utf-8") as fh:
        name2jid = _build_name_to_junction_id(json.load(fh))
    intersections: List[Tuple[str, str]] = []
    for demo in group["intersections"]:
        jid = name2jid.get(demo)
        if jid is None:
            parser.error(f"intersection '{demo}' has no junction_id in "
                         f"{args.intersections}")
        intersections.append((demo, jid))

    print(f"TAZ {args.taz}: {len(intersections)} intersection(s), "
          f"z {args.z:g} m, coverage radius {lidar_params['range']:g} m")
    entries = plan_for_junctions(junctions, intersections,
                                 z=args.z, pitch=args.pitch,
                                 lidar_params=lidar_params)

    if args.print_coverage:
        print_coverage(junctions, intersections, args.z,
                       lidar_params["range"])

    if args.dry_run:
        print(f"\n[dry-run] would write {len(entries)} lidar entry/entries "
              f"to {out}:")
        for e in entries:
            t = e["transform"]
            print(f"  {e['name']}: CARLA ({t['x']:.2f}, {t['y']:.2f}, "
                  f"z={t['z']:g}) range {e['range']:g} m")
        return 0

    replaced = merge_lidar_entries(out, map_name, entries)
    print(f"{len(entries)} lidar entry/entries → {out} "
          f"({replaced} replaced in place)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
