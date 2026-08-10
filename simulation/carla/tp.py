#!/usr/bin/env python3
"""
tp.py — 传送 CARLA spectator 摄像机到指定位置

入参为 CARLA 坐标 (x, y, z) 或路口名(如 demo_1),把 CARLA 窗口的
spectator(观察摄像机)直接传送到地图指定位置。路口名须存在于当前 TAZ
(config/taz.json 中与当前 CARLA 地图同名的 TAZ 组,如 EastZone 地图 →
EastZone 组;地图名匹配不到任何 TAZ 组名时降级为"任一 TAZ 组"校验)。

坐标系统:显式传入的 (x, y, z) 为 **CARLA 坐标**(与 spectator_coords.py
打印的 xyz、CARLA 窗口一致)。路口名模式下内部自动做 SUMO→CARLA 转换
(carla.x = sumo.x, carla.y = -sumo.y,见 README 坐标表);路口本身在
SUMO net.xml 中只有 x/y(z 按路面 waypoint 高度 + --height 计算)。

只在联仿运行时移动 spectator,不动车辆:联仿中车辆由 SUMO 控制,CARLA
侧渲染实体会在每个 tick 被 SUMO 位置覆盖。spectator 是观测者,
``set_transform`` 后下一帧即生效,无需 tick。

用法:
    python tp.py demo_3                       # 传送到 EastZone 组路口 demo_3 正上方 10m 俯视
    python tp.py 21807.28 -42363.87 25.0      # 传送到指定 CARLA 坐标
    python tp.py -- -123.45 -678.90 25.0      # 负数坐标需加 -- 分隔符
    python tp.py --taz WestZone demo_14       # 显式指定 TAZ 组,跳过地图名匹配
    python tp.py --height 30 demo_9           # 更高视角(仅路口名模式生效)
    python tp.py --dry-run demo_3             # 只解析并打印目标,不执行传送
    python tp.py --list                       # 离线列出全部 TAZ 组与组内路口(不连接 CARLA)
    python tp.py --host 192.168.1.100 --port 2000 demo_3

关于同步模式:联仿 CARLA 为同步模式,本脚本**绝不调用 world.tick()**——
第二个客户端 tick 会与 SUMO 失同步(原因见 spectator_coords.py 的
_wait_for_fresh_frame docstring)。传送与帧流动无关,cosim 未运行时照样
设置 spectator,只是画面要等帧流动才可见。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import toolchain_env  # 统一环境配置:env > config/toolchain.json > 自动探测
from xml2odr.batch_clip import (load_config,  # 路口配置(与 run_xml2odr 同源)
                                load_taz_config,  # TAZ 组配置(与 run_xml2odr 同源)
                                _build_name_to_junction_id)


# ---------------------------------------------------------------------------
# CARLA 连接与帧等待(与 spectator_coords.py 同款,原因见其 docstring)
# ---------------------------------------------------------------------------

def _get_map_name(world) -> str:
    """Short CARLA map name (last path segment)."""
    name = world.get_map().name
    return name.split("/")[-1] if "/" in name else name


def _normalize_map_name(raw: str) -> str:
    """去掉 CARLA 地图名多余的后缀段:"EastZone.EastZone" → "EastZone";
    "Town01" 原样返回。CARLA 对自定义地图的 map.name 常为
    "/Game/Carla/Maps/EastZone.EastZone" 形态,``_get_map_name`` 只按 / 切
    末段会留下 ".EastZone" 尾巴,TAZ 组名不带该后缀,需归一化。"""
    seg = raw.split("/")[-1]
    head, sep, tail = seg.rpartition(".")
    if sep and head == tail:
        return head
    return seg


def _wait_for_fresh_frame(world, timeout: float) -> bool:
    """Wait until the client receives a new frame from the server, WITHOUT
    advancing the simulation.

    ``actor.get_transform()`` does not ask the server — it returns the
    transform from the client's cache of the last received frame (per CARLA
    docs: "returns the transform received in the last tick").  A freshly
    connected client has an empty cache and reads (0, 0, 0) until a frame
    arrives; in synchronous mode frames only exist while the co-simulation
    ticks.

    This function must never call ``world.tick()``: a second client ticking a
    synchronous-mode world would advance CARLA independently of SUMO and
    desynchronise the co-simulation.  ``world.wait_for_tick`` only waits for
    the next broadcast frame — it does not tick.

    (copied from spectator_coords.py — teleport only uses it as a
    diagnostics signal, teleport itself does not depend on frames.)

    Returns:
        True when a fresh frame was received within ``timeout`` seconds.
    """
    try:
        snapshot = world.wait_for_tick(timeout=timeout)
        if snapshot is not None:
            return True
    except (AttributeError, TypeError):
        pass  # older API without wait_for_tick → polling grace below

    # Short polling grace for missing/buggy wait_for_tick: frames flowing at
    # ~20Hz arrive within 50ms, so 0.5s is plenty.
    try:
        base_frame = world.get_snapshot().frame
    except Exception:
        base_frame = None
    deadline = time.time() + min(timeout, 0.5)
    while time.time() < deadline:
        try:
            frame = world.get_snapshot().frame
            if base_frame is None or frame != base_frame:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# 入参分类
# ---------------------------------------------------------------------------

def _resolve_target(parser: argparse.ArgumentParser,
                    targets: List[str]) -> Tuple[str, Any]:
    """分类 target 入参:1 个路口名 或 3 个 CARLA 坐标。

    Returns:
        ``("name", name)`` 或 ``("xyz", [x, y, z])``;其余形态
        → ``parser.error``(exit 2)。
    """
    if len(targets) == 1:
        try:
            float(targets[0])
        except ValueError:
            return "name", targets[0]
        parser.error(f"target 是单个数字 '{targets[0]}',"
                     f"需要 1 个路口名或 3 个 CARLA 坐标 X Y Z")
    if len(targets) == 3:
        try:
            return "xyz", [float(v) for v in targets]
        except ValueError:
            pass
    parser.error(f"无法识别 target:收到 {len(targets)} 个参数 "
                 f"({' '.join(targets)});应为 1 个路口名(如 demo_1)"
                 f"或 3 个 CARLA 坐标 X Y Z(负数坐标前加 -- 分隔符,"
                 f"如: python tp.py -- -123.45 -678.90 25.0)")


# ---------------------------------------------------------------------------
# 路口名 → 坐标(config/intersections.json + net.xml 流式扫描)
# ---------------------------------------------------------------------------

def _find_net_file(parser: argparse.ArgumentParser, args,
                   script_dir: str) -> str:
    """路口名模式的 SUMO 路网文件查找。

    候选顺序(第一个存在即用,返回 realpath):
      1. ``--net`` 显式给定(必须存在,否则直接报错,不落候选)
      2. ``toolchain_env.resolve_totalmap_net()``(config/toolchain.json 的
         ``totalmap_net`` > 内置默认 ../../data/maps/sumo/generated/network/
         TotalMap_20.signals.net.xml,与其他工具同源)
      3. ``config/intersections.json`` 的 ``source_net`` 字段(cwd 相对,兼容旧配置)
      4. ``<cwd>/TotalMap.net.xml``(服务器上真实路网就位时命中)

    全部落空 → ``parser.error`` 列出全部候选(exit 2)。
    """
    if args.net:
        if os.path.isfile(args.net):
            return os.path.realpath(args.net)
        parser.error(f"--net 指定的路网文件不存在: {args.net}")

    candidates: List[str] = []
    candidates.append(toolchain_env.resolve_totalmap_net(""))
    try:  # 3) 兼容旧配置:source_net
        src = load_config(args.intersections).get("source_net")
        if src:
            candidates.append(src)
    except Exception:
        pass
    candidates.append(os.path.join(os.getcwd(), "TotalMap.net.xml"))

    for cand in candidates:
        if os.path.isfile(cand):
            return os.path.realpath(cand)

    parser.error("找不到 SUMO 路网文件,已尝试:\n  - "
                 + "\n  - ".join(candidates)
                 + "\n请用 --net 显式指定,或在 config/toolchain.json 配置 totalmap_net。")


def _scan_junctions(net_path: str) -> Dict[str, Dict[str, Any]]:
    """流式扫描 net.xml,只收集 ``<junction id jtype x y>`` 坐标。

    自包含实现,不依赖 plan_lidar_points.scan_net:该函数返回契约跨版本
    漂移(曾返回 (junctions, edges) 二元组,旧服务器版本返回更多值,新版本
    只返回 junctions dict),tp.py 只取 junction 坐标,不复用以免踩坑。
    处理完的元素立即 clear() 释放内存,44MB 级文件秒级完成。
    """
    junctions: Dict[str, Dict[str, Any]] = {}
    for _event, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "junction":
            junctions[elem.get("id")] = {
                "x": float(elem.get("x", 0.0)),
                "y": float(elem.get("y", 0.0)),
                "jtype": elem.get("type", ""),
            }
        elem.clear()
    return junctions


def _junction_coords(net_path: str, junction_id: str) -> Optional[Tuple[float, float]]:
    """在 net.xml 中查 junction 的 SUMO 平面坐标 (x, y)。

    流式扫描(_scan_junctions)不构建完整图模型,44MB 级文件秒级完成。
    返回 None 表示该 junction 不在路网中。
    """
    junctions = _scan_junctions(net_path)
    rec = junctions.get(junction_id)
    if rec is None:
        return None
    return rec["x"], rec["y"]


def _resolve_intersection(parser: argparse.ArgumentParser, args, name: str
                          ) -> Tuple[str, float, float]:
    """路口名 → ``(junction_id, SUMO x, SUMO y)``。

    错误一律 ``parser.error``(exit 2),且区分三种情况:
      1. 名字不在 config/intersections.json 中;
      2. 名字在但 junction_id 为 null(尚未确认);
      3. junction_id 不在 --net 指定的路网中。
    (_build_name_to_junction_id 会静默过滤 null 条目,故直接查原始列表,
    才能区分 1 与 2。)
    """
    try:
        cfg = load_config(args.intersections)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"无法读取路口配置 {args.intersections}: {exc}")

    entry = next((e for e in cfg["intersections"] if e["name"] == name), None)
    if entry is None:
        parser.error(f"路口 '{name}' 不存在于 {args.intersections}"
                     f"(可用 --list 查看全部 TAZ 组及组内路口)")
    if entry.get("junction_id") is None:
        parser.error(f"路口 '{name}' 的 junction_id 为 null(尚未确认),无法定位")

    jid = _build_name_to_junction_id(cfg)[name]
    coords = _junction_coords(args.net, jid)
    if coords is None:
        parser.error(f"junction '{jid}'(路口 {name}) 不在路网 {args.net} 中")
    sx, sy = coords
    return jid, sx, sy


# ---------------------------------------------------------------------------
# TAZ 校验:显式 --taz > 当前地图名同名组 > 任一 TAZ 组(降级)
# ---------------------------------------------------------------------------

def _check_taz(parser: argparse.ArgumentParser, args, map_name: str, name: str
               ) -> Tuple[str, bool]:
    """校验路口 ``name`` 属于"当前 TAZ",返回 ``(组名, 是否降级)``。

    规则:
      1. ``--taz`` 显式:只校验该组(组不存在 → 报错并列出可用组名);
      2. 否则:地图名归一化后在 taz_groups 中精确同名匹配——命中则校验
         ``name ∈ 组.intersections``;
      3. 地图名匹配不到任何 TAZ 组名(如单路口地图 Demo_1_Enhanced):
         警告并降级为"任一 TAZ 组"校验(name ∈ 所有组并集)。

    校验失败 → ``parser.error``(exit 2,列出该组路口清单)。
    """
    try:
        taz_cfg = load_taz_config(args.taz_config)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"无法读取 TAZ 配置 {args.taz_config}: {exc}")
    groups = taz_cfg["taz_groups"]
    group_names = [g["name"] for g in groups]

    if args.taz is not None:
        group = next((g for g in groups if g["name"] == args.taz), None)
        if group is None:
            parser.error(f"--taz 指定的组 '{args.taz}' 不存在"
                         f"(可用组: {', '.join(group_names)})")
        if name not in group["intersections"]:
            parser.error(f"路口 '{name}' 不在 TAZ '{group['name']}' 中"
                         f"(该组路口: {', '.join(group['intersections'])})")
        return group["name"], False

    normalized = _normalize_map_name(map_name)
    group = next((g for g in groups if g["name"] == normalized), None)
    if group is not None:
        if name not in group["intersections"]:
            parser.error(f"路口 '{name}' 不在当前 TAZ '{group['name']}' 中"
                         f"(当前地图 '{map_name}' → TAZ '{normalized}';"
                         f"该组路口: {', '.join(group['intersections'])})")
        return group["name"], False

    # 地图名未匹配任何 TAZ 组名 → 降级为"任一 TAZ 组"校验
    all_intersections = sorted({i for g in groups for i in g["intersections"]})
    print(f"⚠ 地图名 '{map_name}' 未匹配任何 TAZ 组名"
          f"(可用组: {', '.join(group_names)}),降级为'任一 TAZ 组'校验",
          file=sys.stderr)
    if name not in all_intersections:
        parser.error(f"路口 '{name}' 不在任何 TAZ 组中"
                     f"(全部 TAZ 路口: {', '.join(all_intersections)})")
    return "any", True


# ---------------------------------------------------------------------------
# z 处理与传送
# ---------------------------------------------------------------------------

def _road_z(world, x: float, y: float
            ) -> Tuple[Optional[float], Optional[Tuple[float, float]], bool]:
    """目标点路面高度(照搬 run_cosimulation.py:770-795 的做法)。

    waypoint 查询命中 → 路面 z;不在路上 → 从地图拓扑快照到最近道路
    waypoint(返回其 x/y/z);地图无道路拓扑 → 全 None。

    Returns:
        ``(road_z, snapped_xy, snapped)`` — ``snapped_xy`` 为快照后的
        (x, y)(未快照时为原始 (x, y)),``snapped`` 标记是否发生了快照。
    """
    import carla  # noqa: F811 — 惰性导入(连接后才可能用到)

    wp = world.get_map().get_waypoint(carla.Location(x=x, y=y, z=0))
    if wp is not None:
        return wp.transform.location.z, (x, y), False

    # 不在路上 → 快照到最近道路 waypoint(而非悬在空地上方)
    nearest = None
    best_d = float("inf")
    for w1, w2 in world.get_map().get_topology():
        for w in (w1, w2):
            d = ((w.transform.location.x - x) ** 2 +
                 (w.transform.location.y - y) ** 2)
            if d < best_d:
                best_d = d
                nearest = w
    if nearest is not None:
        loc = nearest.transform.location
        return loc.z, (loc.x, loc.y), True
    return None, (x, y), False


def _teleport(spectator, transform, dry_run: bool) -> None:
    """把 spectator 设置到目标位姿。``dry_run`` 时只打印不执行。

    同步模式下 set_transform 无需 tick:spectator 是观测者、非仿真状态,
    下一帧即生效(run_cosimulation 同样直接调用,从不 tick)。
    """
    if dry_run:
        return
    spectator.set_transform(transform)


def _list_taz(parser: argparse.ArgumentParser, args) -> int:
    """离线列出全部 TAZ 组与组内路口(纯 json 读取,不连接 CARLA)。"""
    try:
        taz_cfg = load_taz_config(args.taz_config)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"无法读取 TAZ 配置 {args.taz_config}: {exc}")
    for g in taz_cfg["taz_groups"]:
        print(f"{g['name']}: {', '.join(g['intersections'])}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="*", metavar="TARGET",
                        help="路口名(如 demo_1,须在 config/intersections.json "
                             "中且属于当前 TAZ)或 CARLA 坐标 X Y Z 三个数字。"
                             "坐标为负数时在参数前加 -- 分隔符,如: "
                             "python tp.py -- -123.45 -678.90 25.0")
    parser.add_argument("--host", default="127.0.0.1",
                        help="CARLA server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2000,
                        help="CARLA server port (default: 2000)")
    parser.add_argument("--height", type=float, default=10.0,
                        help="仅路口名模式:路面 waypoint z 之上多少米"
                             "(default: 10,与 run_cosimulation spectator 定位一致)")
    parser.add_argument("--pitch", type=float, default=-90.0,
                        help="俯仰角(度),默认 -90 垂直俯视(default: -90)")
    parser.add_argument("--yaw", type=float, default=0.0, help="偏航角(度)")
    parser.add_argument("--roll", type=float, default=0.0, help="翻滚角(度)")
    parser.add_argument("--net",
                        help="路口名模式的 SUMO 路网 net.xml(默认自动查找: "
                             "intersections.json 的 source_net > "
                             "cwd/TotalMap.net.xml > "
                             "../generated/network/TotalMap_20.signals.net.xml)")
    parser.add_argument("--taz",
                        help="显式指定 TAZ 组名,跳过地图名匹配"
                             "(默认按当前 CARLA 地图名匹配同名组)")
    parser.add_argument("--intersections", default="config/intersections.json",
                        help="demo 名 → junction_id 映射文件"
                             "(default: config/intersections.json)")
    parser.add_argument("--taz-config", default="config/taz.json",
                        help="TAZ 组配置文件(default: config/taz.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="完整解析并打印目标,但不执行传送(仍连接 CARLA,"
                             "以便打印地图名/路面高度/TAZ 命中)")
    parser.add_argument("--list", action="store_true",
                        help="离线列出全部 TAZ 组与组内路口后退出(不连接 CARLA)")
    args = parser.parse_args(argv)

    script_dir = os.path.dirname(os.path.realpath(__file__))

    if args.list:
        return _list_taz(parser, args)

    if not args.target:
        parser.error("缺少 target:需传入 1 个路口名(如 demo_1)"
                     "或 3 个 CARLA 坐标 X Y Z(或 --list)")
    if args.height <= 0:
        parser.error("--height 必须 > 0")

    # ── 参数合法性检查(exit 2 类)全部在连接 CARLA 之前完成:
    #    形态非法 / 路口名不存在 / junction_id null / net 找不到,
    #    都不需要连模拟器即可报错。TAZ 校验与 waypoint 查询需地图名,
    #    留在连接之后。──
    mode, payload = _resolve_target(parser, args.target)
    if mode == "name":
        args.net = _find_net_file(parser, args, script_dir)
        jid, sx, sy = _resolve_intersection(parser, args, payload)
    else:  # xyz
        jid, sx, sy = None, payload[0], -payload[1]

    toolchain_env.add_carla_pythonapi_to_path()
    try:
        import carla  # lazy import — only needed at runtime
    except ImportError:
        print("ERROR: carla Python API not found. "
              "CARLA 源码目录仅从环境变量 CARLA_ROOT 或 config/toolchain.json 的 "
              "carla_root 解析(无硬编码路径)。\n"
              "Add CARLA's PythonAPI/carla/dist/carla-*.egg to PYTHONPATH, "
              "or: pip install carla==<your-carla-version>", file=sys.stderr)
        return 1

    try:
        client = carla.Client(args.host, args.port, worker_threads=1)
        client.set_timeout(10.0)
        world = client.get_world()
    except Exception as exc:
        print(f"ERROR: cannot connect to CARLA at {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        return 1

    spectator = world.get_spectator()
    map_name = _get_map_name(world)

    # ── 连接诊断行(与 spectator_coords.py 一致)──
    sync_mode = False
    try:
        sync_mode = bool(world.get_settings().synchronous_mode)
    except Exception:
        pass
    last_frame = None
    try:
        last_frame = world.get_snapshot().frame
    except Exception:
        pass
    diag = f"sync_mode={'ON' if sync_mode else 'OFF'}"
    if last_frame is not None:
        diag += f" | last_frame={last_frame}"
    print(f"Connected to CARLA {client.get_server_version()} | "
          f"map: {map_name} | {diag}", flush=True)

    # 等待一帧仅作诊断:传送本身与帧无关,同步模式没人 tick 时警告但不中断
    fresh = _wait_for_fresh_frame(world, timeout=2.0)
    if not fresh and sync_mode:
        print("⚠ no fresh frame received — the world is in synchronous mode "
              "and nobody is ticking it (is the co-simulation running?). "
              "Teleport still takes effect on the next frame.", flush=True)

    if mode == "name":
        name = payload
        taz_name, taz_fallback = _check_taz(parser, args, map_name, name)
        cx, cy = sx, -sy  # SUMO → CARLA:carla = (sumo_x, -sumo_y),README 坐标表
        road_z, snapped_xy, snapped = _road_z(world, cx, cy)
        if snapped:
            cx, cy = snapped_xy
            print(f"⚠ 目标点不在道路上,已快照到最近道路 waypoint "
                  f"({cx:.2f}, {cy:.2f})", file=sys.stderr)
        if road_z is None:
            cz = args.height
            print(f"⚠ 地图无道路拓扑,无法查询路面高度,"
                  f"使用绝对高度 z={args.height}", file=sys.stderr)
        else:
            cz = road_z + args.height
    else:  # xyz — 坐标模式:全部用给定值,不做 TAZ 校验与 waypoint 查询
        cx, cy, cz = payload
        taz_name, taz_fallback = None, False
        road_z, snapped = None, False
        if args.height != 10.0:
            print(f"忽略 --height {args.height}(坐标模式 z 取给定值)",
                  file=sys.stderr)
        if args.taz is not None:
            print(f"忽略 --taz {args.taz}(坐标模式不做 TAZ 校验)", file=sys.stderr)

    transform = carla.Transform(
        carla.Location(x=cx, y=cy, z=cz),
        carla.Rotation(pitch=args.pitch, yaw=args.yaw, roll=args.roll),
    )
    _teleport(spectator, transform, args.dry_run)

    ts = time.strftime("%H:%M:%S")
    if mode == "name":
        taz_desc = (f"taz={taz_name}"
                    f"({'任意组降级' if taz_fallback else 'match'})")
        print(f"[{ts}] map={map_name}  target={name}  {taz_desc}  "
              f"junction={jid}")
    else:
        print(f"[{ts}] map={map_name}  target=xyz  taz=n/a(坐标模式)")
    print(f"  carla xyz=({cx:8.2f}, {cy:8.2f}, {cz:6.2f})  "
          f"rot=(pitch={args.pitch:6.2f}, yaw={args.yaw:6.2f}, "
          f"roll={args.roll:6.2f})")
    parts = [f"sumo=({sx:8.2f}, {sy:8.2f})"]
    if mode == "name":
        parts.append(f"net={args.net}")
        if road_z is not None:
            parts.append(f"road z={road_z:.2f}")
            parts.append(f"camera height={args.height:.2f}")
        if snapped:
            parts.append("(snapped to nearest road)")
    print("  " + "  ".join(parts))
    print("Teleported spectator." if not args.dry_run
          else "[dry-run] 未执行传送", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
