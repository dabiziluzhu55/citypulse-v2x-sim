#!/usr/bin/env python3
"""
validate.py — 地图联仿有效性校验(替代 verify_xodr_alignment.py)

输入:只需地图名,自动在固定目录中寻找同名文件:
    <carla-dir>/<name>/<name>.xodr   —— RoadRunner 编辑后导出的 OpenDRIVE 文件
                                    (默认 ../../data/maps/carla,可经 config/toolchain.json
                                     maps_carla_dir 配置)
    <net-dir>/<name>/<name>.clipped.net.xml —— SUMO 路网真值(裁剪子网或大地图,
                                    默认 ../../data/maps/xodr,可经 maps_xodr_dir 配置)

输出:三项检查的分级结论 + 可操作建议,退出码 0/1/2/3:
    [A 对齐]   xodr 路口中心 vs net.xml junction 节点,系统性偏移
               < 3m → ⚠️ 噪声内偏差(告知);3–10m → ⚠️ 系统性偏移(可联仿,建议确认);
               ≥ 10m → ❌ 联仿不可用(车辆将明显偏离道路)
    [B 拓扑]   逐路口对比手臂数、各臂入向车道数、转向连接关系
    [C round-trip] 用 CARLA 官方联仿工具 (../Sumo/) 的 netconvert 命令把 xodr
               重转为 net.xml,与参考路网对比覆盖率与拓扑(官方工具视角的诊断参考,
               SUMO 主导模式下覆盖率不足不阻断联仿)
    0=OK  1=WARNING  2=ERROR  3=运行故障(环境/文件/解析/netconvert 崩溃)

用法:
    python validate.py EastZone
    python validate.py EastZone --json          # 机器报告写 stdout,人类文本写 stderr
    python validate.py EastZone --carla-dir ../../data/maps/carla --net-dir ../../data/maps/xodr

说明:仅在服务器上运行(需要 SUMO_HOME 与 netconvert)。检查 C 的 netconvert
参数与 ../Sumo/util/netconvert_carla.py 保持一致(几何重采样、typ.xml 车道丢弃、
--output.original-names、--tls.discard-loaded),即"以官方联仿工具的视角"判断。
检查 C 仅作诊断参考:官方 OpenDRIVE 导入有损(转向连接缺失/路口位置偏移等已知限制),
低覆盖率属常态;联仿为 SUMO 主导(路由由 net.xml 决定),覆盖率不足只给 WARNING。

误判对策:
    * netconvert/RoadRunner 的合理差异(车道类型丢弃、几何重采样)→ 只比 driving
      类车道、只比路口拓扑,不做逐点几何对比
    * RoadRunner 重命名路口(J148 型)→ name 匹配失败自动降级为几何近邻匹配
    * 小地图均值偏移不稳 → 保留每路口明细表 + 散布警告,判定用系统性均值
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# ── 常量 ──────────────────────────────────────────────────────────────────────
# 与 ../Sumo/util/data/opendrive_netconvert.typ.xml 的"不丢弃"乘用车车道类型一致
# (none/shoulder/border/median/special* 在官方工具中被 discard,两侧都排除)
DRIVING_TYPES = {"driving", "entry", "exit", "onRamp", "offRamp"}
SPREAD_WARN_M = 4.0        # 对齐偏移散布警告阈值(米),沿用旧脚本
REGION_MARGIN_M = 10.0     # 区域外扩(米):判定"参考 net 中可对比路口"的边界
SCENE_EDGE_REACH_M = 50.0  # xodr 道路端点"到达"判据:路口距某道路端点小于此值
                           # 视为场景边界断头(裁剪边界,联仿场景门控会处理),不算拓扑缺失
NETCONVERT_TIMEOUT_S = 600
EXIT_OK, EXIT_WARNING, EXIT_ERROR, EXIT_OPERATIONAL = 0, 1, 2, 3

LEVELS = {"OK": 0, "WARNING": 1, "ERROR": 2}
LEVEL_ICON = {"OK": "✅", "WARNING": "⚠️", "ERROR": "❌"}


# ── 数据模型 ─────────────────────────────────────────────────────────────────
@dataclass
class XodrJunction:
    id: str
    name: str | None
    center: tuple[float, float]
    n: int                                # 参与中心估计的道路端点个数(≥3 置信)
    in_roads: list[str] = field(default_factory=list)   # successor 指向本路口
    out_roads: list[str] = field(default_factory=list)  # predecessor 为本路口
    connections: list[tuple[str, str]] = field(default_factory=list)  # (in_road, out_road)


@dataclass
class XodrMap:
    junctions: dict[str, XodrJunction]          # xodr junction id -> junction
    by_name: dict[str, str]                     # junction name(SUMO ID) -> xodr id
    by_road: dict[str, dict]                    # road id -> {driving_lanes, start, end}
    bbox: tuple[float, float, float, float]     # (north, south, east, west)
    offset: tuple[float, float]                 # <offset> x/y(默认 0,0)


@dataclass
class NetJunction:
    id: str
    x: float
    y: float
    type: str


@dataclass
class NetEdge:
    id: str
    from_j: str
    to_j: str
    function: str
    start: tuple[float, float] | None
    end: tuple[float, float] | None
    driving_lanes: int


@dataclass
class NetTopology:
    junctions: dict[str, NetJunction]
    edges: dict[str, NetEdge]
    connections: list[tuple[str, str]]          # (from_edge, to_edge)
    tl_logics: set[str]
    bbox: tuple[float, float, float, float]


@dataclass
class JunctionMatch:
    xodr_id: str
    net_id: str
    method: str          # "name" | "id" | "geo"
    dist_m: float


@dataclass
class Finding:
    junction: str
    method: str
    level: str
    kind: str            # 机器可读的分类
    detail: str          # 人类可读的中文描述
    data: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    level: str
    message: str
    findings: list[Finding] = field(default_factory=list)
    data: dict = field(default_factory=dict)


# ── xodr 解析(复用 verify_xodr_alignment.py 的几何与中心估计算法)─────────────
def geo_end(geo: ET.Element, length: float) -> tuple[float, float]:
    """计算 planView 几何元素终点 (x, y)。支持 line / arc / paramPoly3 / poly3,
    其余类型(spiral 等)按直线近似——仅用于路口中心估计,误差可忽略。"""
    x, y = float(geo.get("x")), float(geo.get("y"))
    hdg = float(geo.get("hdg"))
    typ = geo[0].tag if len(geo) else None
    if typ == "line":
        return (x + length * math.cos(hdg), y + length * math.sin(hdg))
    if typ == "arc":
        c = float(geo[0].get("curvature"))
        if abs(c) < 1e-12:
            return (x + length * math.cos(hdg), y + length * math.sin(hdg))
        return (x + (math.sin(hdg + length * c) - math.sin(hdg)) / c,
                y - (math.cos(hdg + length * c) - math.cos(hdg)) / c)
    if typ == "paramPoly3":
        p = geo[0]
        aU, bU, cU, dU = (float(p.get(k)) for k in ("aU", "bU", "cU", "dU"))
        aV, bV, cV, dV = (float(p.get(k)) for k in ("aV", "bV", "cV", "dV"))
        l = 1.0 if p.get("pRange", "normalized") == "normalized" else length
        u = aU + bU * l + cU * l * l + dU * l * l * l
        v = aV + bV * l + cV * l * l + dV * l * l * l
        return (x + u * math.cos(hdg) - v * math.sin(hdg),
                y + u * math.sin(hdg) + v * math.cos(hdg))
    if typ == "poly3":
        p = geo[0]
        a, b, c, d = (float(p.get(k)) for k in ("a", "b", "c", "d"))
        v = a + b * length + c * length * length + d * length ** 3
        return (x + length * math.cos(hdg) - v * math.sin(hdg),
                y + length * math.sin(hdg) + v * math.cos(hdg))
    # spiral 等:直线近似
    return (x + length * math.cos(hdg), y + length * math.sin(hdg))


def _road_endpoints(road: ET.Element) -> tuple[tuple[float, float], tuple[float, float]]:
    """road 的 (起点, 终点):起点=首个 geometry 的 x/y,终点=geo_end 链式累积。"""
    geos = list(road.find("planView"))
    if not geos:
        return ((0.0, 0.0), (0.0, 0.0))
    start = (float(geos[0].get("x")), float(geos[0].get("y")))
    end = start
    for g in geos:
        end = geo_end(g, float(g.get("length")))
    return start, end


def _section_driving_lanes(section: ET.Element | None, side: str) -> int:
    """laneSection 中某侧(side="left"/"right")的 driving 车道数(排除中心车道)。"""
    if section is None:
        return 0
    s = section.find(side)
    if s is None:
        return 0
    return sum(1 for ln in s.findall("lane")
               if ln.get("id") != "0" and ln.get("type") in DRIVING_TYPES)


def _road_incoming_lanes(road: ET.Element, approach_at_end: bool) -> int:
    """road 在邻接路口端的"入向" driving 车道数。

    OpenDRIVE 约定:right 侧车道沿 s+ 方向行驶,left 侧沿 s- 方向。
    - successor 指向路口(道路终点在路口):入向 = 末段 right 侧
    - predecessor 为路口(道路起点在路口):入向 = 首段 left 侧
    """
    # laneSection 嵌套在 <lanes> 下,用 iter 全量查找(RoadRunner 导出的标准结构)
    sections = list(road.iter("laneSection"))
    if not sections:
        return 0
    if approach_at_end:
        return _section_driving_lanes(sections[-1], "right")
    return _section_driving_lanes(sections[0], "left")


def load_xodr_topology(xodr_path: str) -> XodrMap:
    """解析 xodr 的路口/道路/连接拓扑。junctions 按 xodr id 组织,by_name 提供
    SUMO ID 索引(RoadRunner 导出时 <junction name> 保留 SUMO junction ID)。"""
    tree = ET.parse(xodr_path)
    root = tree.getroot()

    # 头部
    header = root.find("header")
    off = header.find("offset") if header is not None else None
    offset = ((float(off.get("x")), float(off.get("y"))) if off is not None else (0.0, 0.0))
    bbox = (float(header.get("north")), float(header.get("south")),
            float(header.get("east")), float(header.get("west"))) if header is not None \
        else (float("inf"), float("-inf"), float("-inf"), float("inf"))

    # 道路端点与入向车道
    by_road: dict[str, dict] = {}
    for road in root.findall("road"):
        rid = road.get("id")
        start, end = _road_endpoints(road)
        link = road.find("link")
        links = []
        if link is not None:
            links = [(el.tag, el.get("elementType"), el.get("elementId")) for el in link]
        approach_at_end = any(t == "successor" and et == "junction" for t, et, _ in links)
        by_road[rid] = {
            "start": start, "end": end,
            "driving_lanes": _road_incoming_lanes(road, approach_at_end),
            "links": links,
        }

    # 路口:中心 = 邻接道路端点均值(仅 junction 型 link 参与;道路双向只计一次)
    name_of: dict[str, str] = {}   # junction name(SUMO ID) -> xodr junction id
    for j in root.findall("junction"):
        name = j.get("name")
        if name:
            name_of[name] = j.get("id")
    pts: dict[str, list[tuple[float, float]]] = {}
    in_roads: dict[str, set[str]] = {}
    out_roads: dict[str, set[str]] = {}
    for rid, info in by_road.items():
        for tag, etype, eid in info["links"]:
            if etype != "junction":
                continue
            pts.setdefault(eid, []).append(info["end"] if tag == "successor" else info["start"])
            if tag == "successor":
                in_roads.setdefault(eid, set()).add(rid)
            else:
                out_roads.setdefault(eid, set()).add(rid)

    junctions: dict[str, XodrJunction] = {}
    for j in root.findall("junction"):
        jid = j.get("id")
        conns: list[tuple[str, str]] = []
        for c in j.findall("connection"):
            in_r = c.get("incomingRoad")
            out_r = None
            cr = by_road.get(c.get("connectingRoad"))
            if cr is not None:
                for tag, etype, eid in cr["links"]:
                    if tag == "successor" and etype == "road":
                        out_r = eid
                        break
            if in_r and out_r and (in_r, out_r) not in conns:
                conns.append((in_r, out_r))
        p = pts.get(jid, [])
        center = ((sum(q[0] for q in p) / len(p), sum(q[1] for q in p) / len(p))
                  if p else (0.0, 0.0))
        junctions[jid] = XodrJunction(
            id=jid, name=j.get("name"), center=center, n=len(p),
            in_roads=sorted(in_roads.get(jid, [])),
            out_roads=sorted(out_roads.get(jid, [])),
            connections=conns)
    return XodrMap(junctions=junctions, by_name=name_of, by_road=by_road,
                   bbox=bbox, offset=offset)


# ── net.xml 解析(流式,48MB 文件内存安全)──────────────────────────────────────
def _parse_shape(shape_attr: str | None) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """shape 字符串 -> (首点, 末点);无 shape 或点数不足返回 None。"""
    if not shape_attr:
        return None
    pts = shape_attr.split()
    if len(pts) < 2:
        return None
    first = tuple(float(v) for v in pts[0].split(","))
    last = tuple(float(v) for v in pts[-1].split(","))
    return (first, last)  # type: ignore[return-value]


def _lane_is_driving(lane: ET.Element) -> bool:
    """车道可行驶判定:disallow 不含 "all"/"passenger"(与实测 disallow 直方图对应)。"""
    dis = lane.get("disallow") or ""
    return not ("all" in dis or "passenger" in dis)


def load_net_topology(net_path: str) -> NetTopology:
    """流式解析 SUMO net.xml:junctions(排除 internal)、edges(含首尾 shape 点、
    driving 车道数)、connections、tlLogic。"""
    junctions: dict[str, NetJunction] = {}
    edges: dict[str, NetEdge] = {}
    connections: list[tuple[str, str]] = []
    tl_logics: set[str] = set()
    xs, ys = [], []
    for _, el in ET.iterparse(net_path, events=("end",)):
        if el.tag == "junction":
            jtype = el.get("type", "")
            if jtype == "internal":
                el.clear()
                continue
            jid = el.get("id")
            x, y = float(el.get("x")), float(el.get("y"))
            junctions[jid] = NetJunction(id=jid, x=x, y=y, type=jtype)
            xs.append(x)
            ys.append(y)
        elif el.tag == "edge":
            eid = el.get("id")
            func = el.get("function", "")
            lanes = 0
            for ln in el:
                if ln.tag == "lane" and _lane_is_driving(ln):
                    lanes += 1
            sp = _parse_shape(el.get("shape"))
            edges[eid] = NetEdge(id=eid, from_j=el.get("from", ""), to_j=el.get("to", ""),
                                 function=func,
                                 start=sp[0] if sp else None,
                                 end=sp[1] if sp else None,
                                 driving_lanes=lanes)
        elif el.tag == "connection":
            connections.append((el.get("from", ""), el.get("to", "")))
        elif el.tag == "tlLogic":
            tl_logics.add(el.get("id", ""))
        el.clear()
    if xs:
        bbox = (max(ys), min(ys), max(xs), min(xs))
    else:
        bbox = (float("inf"), float("-inf"), float("-inf"), float("inf"))
    return NetTopology(junctions=junctions, edges=edges, connections=connections,
                       tl_logics=tl_logics, bbox=bbox)


# ── 路口匹配(name → id → 几何)────────────────────────────────────────────────
def match_junctions(xodr_map: XodrMap, net: NetTopology,
                    offset: tuple[float, float] = (0.0, 0.0),
                    radius: float = 5.0) -> list[JunctionMatch]:
    """三级匹配:
    ① name:xodr <junction name> == net junction id(RoadRunner 保留 SUMO ID)
    ② id:xodr junction id == net id(仅限该 xodr junction 无 name,且距离合理,
       避免 RoadRunner 的 id="119" name="3864" 与无关的 net junction "119" 错配)
    ③ geo:贪心最近对(距离 = xodr 中心 + offset 与 net 节点,radius 内,每侧只用一次)
    """
    matches: list[JunctionMatch] = []
    used_x: set[str] = set()
    used_n: set[str] = set()

    for name, xid in xodr_map.by_name.items():
        if name in net.junctions:
            matches.append(JunctionMatch(xodr_id=xid, net_id=name, method="name", dist_m=0.0))
            used_x.add(xid)
            used_n.add(name)

    for xid, xj in xodr_map.junctions.items():
        if xid in used_x or xj.name is not None:
            continue
        nj = net.junctions.get(xid)
        if nj is None or xid in used_n:
            continue
        d = math.hypot(xj.center[0] + offset[0] - nj.x, xj.center[1] + offset[1] - nj.y)
        if d <= radius:
            matches.append(JunctionMatch(xodr_id=xid, net_id=xid, method="id", dist_m=d))
            used_x.add(xid)
            used_n.add(xid)

    pairs = []
    for xid, xj in xodr_map.junctions.items():
        if xid in used_x:
            continue
        cx, cy = xj.center[0] + offset[0], xj.center[1] + offset[1]
        for nid, nj in net.junctions.items():
            if nid in used_n:
                continue
            d = math.hypot(cx - nj.x, cy - nj.y)
            if d <= radius:
                pairs.append((d, xid, nid))
    for d, xid, nid in sorted(pairs):
        if xid in used_x or nid in used_n:
            continue
        matches.append(JunctionMatch(xodr_id=xid, net_id=nid, method="geo", dist_m=d))
        used_x.add(xid)
        used_n.add(nid)
    return matches


# ── 检查 A:坐标对齐(沿用旧脚本判定逻辑)───────────────────────────────────────
def check_alignment(xodr_map: XodrMap, net: NetTopology, matches: list[JunctionMatch],
                    threshold: float, error_threshold: float) -> tuple[CheckResult, tuple[float, float]]:
    exact = [m for m in matches if m.method in ("name", "id")]
    if not exact:
        res = CheckResult(level="WARNING", message="无法对比:两侧无任何同名/同ID路口可核对坐标。",
                          data={"matched_exact": 0})
        return res, (0.0, 0.0)

    rows = []
    deltas = []
    for m in exact:
        xj = xodr_map.junctions[m.xodr_id]
        nj = net.junctions[m.net_id]
        dx, dy = xj.center[0] - nj.x, xj.center[1] - nj.y
        deltas.append((dx, dy))
        rows.append({"junction": (xj.name or m.net_id), "method": m.method,
                     "sumo": [nj.x, nj.y], "xodr": [xj.center[0], xj.center[1]],
                     "delta_m": [dx, dy], "dist_m": math.hypot(dx, dy),
                     "arms": len(set(xj.in_roads) | set(xj.out_roads)), "n": xj.n})
    rows.sort(key=lambda r: -r["dist_m"])

    dxs = [d[0] for d in deltas]
    dys = [d[1] for d in deltas]
    dxm, dym = statistics.mean(dxs), statistics.mean(dys)
    spread = (max(dxs) - min(dxs), max(dys) - min(dys))
    mean_off = math.hypot(dxm, dym)
    offset = (dxm, dym)

    notes = []
    if spread[0] > SPREAD_WARN_M or spread[1] > SPREAD_WARN_M:
        notes.append(f"偏移散布较大(dx={spread[0]:.2f}, dy={spread[1]:.2f}m > {SPREAD_WARN_M:.0f}m)"
                     "——路口中心估计带 ±1–3m 路口盒偏差,超过 4m 提示存在旋转/缩放,均值仅供参考。")

    if mean_off >= error_threshold:
        level = "ERROR"
        message = (f"仍偏移:系统性偏移 {mean_off:.2f}m ≥ {error_threshold:.0f}m"
                   "—— 地图尚未回到 SUMO 内部坐标系,联仿时车辆将明显偏离道路。")
        # offset = xodr - sumo,修复需按相反方向平移场景
        suggestion = (f"建议平移 (dx, dy) = ({-dxm:+.1f}, {-dym:+.1f}):"
                      "RoadRunner 中把整张场景平移回 SUMO 内部坐标系后重新导出并重新导入 CARLA。"
                      f"(当前偏移 xodr−sumo = ({dxm:+.1f}, {dym:+.1f}),按相反方向平移)")
        notes.insert(0, suggestion)
    elif mean_off >= threshold:
        level = "WARNING"
        message = (f"存在系统性偏移 {mean_off:.2f}m({threshold:.0f}–{error_threshold:.0f}m 区间)"
                   "—— 不影响联仿正常运行,车辆可能略有横向偏差,建议人工确认。")
        suggestion = (f"如需修正,建议平移 (dx, dy) = ({-dxm:+.1f}, {-dym:+.1f})"
                      f"(当前偏移 xodr−sumo = ({dxm:+.1f}, {dym:+.1f}),按相反方向平移)")
        notes.insert(0, suggestion)
    else:
        worst = max(math.hypot(*d) for d in deltas)
        level = "WARNING"
        message = (f"已对齐:系统性偏移 {mean_off:.2f}m 在路口中心估计噪声范围内"
                   f"(< {threshold:.0f}m;单路口最大偏差 {worst:.2f}m 为估计噪声)"
                   "—— 偏差存在但不影响联仿,无需补偿参数。")
        suggestion = ""
    if notes:
        message += " " + " ".join(notes)
    res = CheckResult(level=level, message=message,
                      data={"threshold_m": threshold, "error_threshold_m": error_threshold,
                            "matched_exact": len(exact),
                            "offset_m": {"dx": dxm, "dy": dym},
                            "spread_m": {"dx": spread[0], "dy": spread[1]},
                            "suggestion": suggestion,
                            "junction_deltas": rows})
    return res, offset


# ── 检查 B:路口拓扑(手臂/车道/连接)──────────────────────────────────────────
def _bearing(p_from: tuple[float, float], p_to: tuple[float, float]) -> float | None:
    """方位角(度,0=东)。仅用于相对比较,方向约定无关紧要。"""
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    return math.degrees(math.atan2(dy, dx))


def _line_angle(start: tuple[float, float] | None,
                end: tuple[float, float] | None) -> float | None:
    """手臂轴线方位角(度,mod 180):由远端点→近路口端点的连线方向取模 180。
    与节点→路口盒边点的方向相比,轴线对路口盒大小不敏感(噪声远小),
    且入向/出向同一物理臂的轴线角相同(相差 180° 取模后相等)。"""
    if start is None or end is None:
        return None
    a = _bearing(start, end)
    if a is None:
        return None
    return a % 180.0


def _bearing_delta(a: float, b: float) -> float:
    """圆形方位角差(0~180):轴线角 0° 与 179.9° 实际只差 0.1°。"""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _greedy_match_by_bearing(a_list: list[tuple[str, float]], b_list: list[tuple[str, float]],
                             tol_deg: float) -> dict[str, str]:
    """按(圆形)方位角贪心配对,返回 {a_id: b_id}(tol_deg 内,每侧只用一次)。"""
    pairs = sorted(((_bearing_delta(a[1], b[1]), a[0], b[0]) for a in a_list for b in b_list))
    used_b: set[str] = set()
    result: dict[str, str] = {}
    for d, a, b in pairs:
        if d > tol_deg or a in result or b in used_b:
            continue
        result[a] = b
        used_b.add(b)
    return result


def _net_arms(net: NetTopology, jid: str) -> dict[str, dict]:
    """net 侧路口手臂:仅入边(to==J,排除 internal)——每个物理臂一条入边,
    与 xodr 的"入向道路"一一对应,避免把出边误当作手臂。"""
    arms: dict[str, dict] = {}
    for e in net.edges.values():
        if e.function == "internal" or e.to_j != jid:
            continue
        arms[e.id] = {"bearing": _line_angle(e.start, e.end),
                      "in_lanes": e.driving_lanes}
    return arms


def _xodr_arms(xodr_map: XodrMap, jid: str) -> dict[str, dict]:
    """xodr 侧路口手臂:仅入向道路(successor 指向本路口)。轴线角 = 道路
    起点→终点方向 mod 180(入向道路终点在路口,s+ 即入向)。"""
    xj = xodr_map.junctions[jid]
    arms: dict[str, dict] = {}
    for rid in xj.in_roads:
        info = xodr_map.by_road[rid]
        arms[rid] = {"bearing": _line_angle(info["start"], info["end"]),
                     "in_lanes": info["driving_lanes"]}
    return arms


def region_in_net_frame(bbox: tuple[float, float, float, float],
                        offset: tuple[float, float]) -> tuple[float, float, float, float]:
    """xodr 帧场景范围(bbox=(north,south,east,west))换算到 net 帧。

    offset 约定:offset = xodr - net(xodr 坐标 = net 坐标 + offset),
    故 net 坐标 = xodr 坐标 - offset。"""
    north, south, east, west = bbox
    dx, dy = offset
    return (north - dy, south - dy, east - dx, west - dx)


def _junction_in_region(net: NetTopology, jid: str,
                        region: tuple[float, float, float, float]) -> bool:
    """路口是否位于(偏移补偿后的)场景区域内。用于排除场景边界外的手臂——
    参考 net 大于 xodr 场景时,边界手臂在 xodr 中本就应缺失(裁剪边界/场景外),
    不应算作"拓扑被更改"。"""
    j = net.junctions.get(jid)
    if j is None:
        return True  # 未知路口(畸形数据)保守视为区域内
    north, south, east, west = region
    return (south - REGION_MARGIN_M <= j.y <= north + REGION_MARGIN_M
            and west - REGION_MARGIN_M <= j.x <= east + REGION_MARGIN_M)


def _net_outgoing(net: NetTopology, jid: str) -> dict[str, set[str]]:
    """net 侧:入边 -> 可到达的出边集合(经 internal 边 2 跳解析)。"""
    from_to: dict[str, list[str]] = {}
    for f, t in net.connections:
        from_to.setdefault(f, []).append(t)
    outgoing: dict[str, set[str]] = {}
    for in_e, tos in from_to.items():
        if in_e not in net.edges or net.edges[in_e].to_j != jid:
            continue
        outs: set[str] = set()
        for t1 in tos:
            e1 = net.edges.get(t1)
            if e1 is None:
                continue
            if e1.function != "internal":
                outs.add(t1)
            else:
                for t2 in from_to.get(t1, []):
                    e2 = net.edges.get(t2)
                    if e2 is not None and e2.function != "internal" and e2.from_j == jid:
                        outs.add(t2)
        if outs:
            outgoing[in_e] = outs
    return outgoing


def _xodr_outgoing(xodr_map: XodrMap, jid: str) -> dict[str, set[str]]:
    """xodr 侧:入向道路 -> 可到达的出向道路集合(connection 的 connectingRoad 的
    successor 即出口臂)。"""
    xj = xodr_map.junctions[jid]
    outgoing: dict[str, set[str]] = {}
    for in_r, out_r in xj.connections:
        outgoing.setdefault(in_r, set()).add(out_r)
    return outgoing


def check_topology(xodr_map: XodrMap, net: NetTopology, matches: list[JunctionMatch],
                   offset: tuple[float, float], lane_tol: int, bearing_tol: float,
                   region_bbox: tuple[float, float, float, float]) -> CheckResult:
    """逐匹配路口对比手臂数 / 各臂入向车道数 / 连接关系。

    region_bbox 为 xodr 场景范围(xodr 坐标系),偏移补偿后用于圈定
    "参考 net 中可对比的路口"(xodr 之外的 net 路口本就不可比)。"""
    findings: list[Finding] = []
    matched_net = set()
    dx0, dy0 = offset
    region = region_in_net_frame(region_bbox, offset)
    for m in matches:
        xj = xodr_map.junctions[m.xodr_id]
        nj = net.junctions[m.net_id]
        matched_net.add(m.net_id)
        x_arms = _xodr_arms(xodr_map, m.xodr_id)
        n_arms_all = _net_arms(net, m.net_id)
        # 参考 net 大于场景时,排除远端路口在场景区域外的边界手臂
        n_arms = {eid: a for eid, a in n_arms_all.items()
                  if _junction_in_region(net, net.edges[eid].from_j, region)}
        x_out = _xodr_outgoing(xodr_map, m.xodr_id)
        n_out_all = _net_outgoing(net, m.net_id)
        n_out = {}
        for in_e, outs in n_out_all.items():
            if not _junction_in_region(net, net.edges[in_e].from_j, region):
                continue
            n_out[in_e] = {o for o in outs
                           if _junction_in_region(net, net.edges[o].to_j, region)}
        arm_pairs = _greedy_match_by_bearing(
            [(a, ar["bearing"] or 0.0) for a, ar in x_arms.items()],
            [(a, ar["bearing"] or 0.0) for a, ar in n_arms.items()],
            bearing_tol)
        if len(x_arms) != len(n_arms):
            excluded = len(n_arms_all) - len(n_arms)
            suffix = f"(另排除场景外边界手臂 {excluded} 条)" if excluded else ""
            findings.append(Finding(
                junction=xj.name or m.net_id, method=m.method, level="ERROR", kind="arm_count",
                detail=f"手臂数不一致:xodr={len(x_arms)}, net={len(n_arms)}{suffix}"
                       "——SUMO 中可通行的方向在 CARLA 地图中缺失,车辆将偏离道路",
                data={"xodr_arms": len(x_arms), "net_arms": len(n_arms),
                      "net_arms_excluded_boundary": excluded,
                      "xodr_arms_list": list(x_arms), "net_arms_list": list(n_arms)}))
        unmatched_x = [a for a in x_arms if a not in arm_pairs]
        unmatched_n = [a for a in n_arms if a not in arm_pairs.values()]
        if unmatched_x or unmatched_n:
            findings.append(Finding(
                junction=xj.name or m.net_id, method=m.method, level="WARNING",
                kind="arm_unmatched",
                detail=f"无法按方位角配对的手臂:{unmatched_x or []} / {unmatched_n or []}",
                data={"unmatched_x": unmatched_x, "unmatched_n": unmatched_n}))
        for xa, na in arm_pairs.items():
            xl = x_arms[xa]["in_lanes"]
            nl = n_arms[na]["in_lanes"]
            if abs(xl - nl) > lane_tol:
                findings.append(Finding(
                    junction=xj.name or m.net_id, method=m.method, level="WARNING",
                    kind="lane_count",
                    detail=f"臂 {xa} 入向 driving 车道数 xodr={xl} vs net={nl}(差 > {lane_tol})",
                    data={"arm": xa, "xodr_lanes": xl, "net_lanes": nl}))
        # 连接关系
        for in_a, na in arm_pairs.items():
            if na not in n_out:
                continue
            x_outs = x_out.get(in_a, set())
            n_outs = n_out[na]
            if not x_outs:
                findings.append(Finding(
                    junction=xj.name or m.net_id, method=m.method, level="ERROR",
                    kind="dead_end",
                    detail=f"臂 {in_a} 在 SUMO 中有 {len(n_outs)} 个出口连接,但 xodr 中没有任何连接"
                           "——该方向车辆在路口将无路可走(拓扑被破坏)",
                    data={"arm": in_a, "net_outgoing": sorted(n_outs)}))
                continue
            # 出口臂按轴线角对比(mod 180,对路口盒大小不敏感)
            x_out_b = {}
            for o in x_outs:
                info = xodr_map.by_road.get(o)
                if info is not None:
                    ang = _line_angle(info["start"], info["end"])
                    if ang is not None:
                        x_out_b[o] = ang
            n_out_b = {}
            for o in n_outs:
                e = net.edges.get(o)
                if e is not None:
                    ang = _line_angle(e.start, e.end)
                    if ang is not None:
                        n_out_b[o] = ang
            pairs = _greedy_match_by_bearing(list(x_out_b.items()), list(n_out_b.items()), bearing_tol)
            miss_x = [o for o in x_outs if o not in pairs]
            miss_n = [o for o in n_outs if o not in pairs.values()]
            if miss_n or miss_x:
                findings.append(Finding(
                    junction=xj.name or m.net_id, method=m.method, level="WARNING",
                    kind="connection_mismatch",
                    detail=f"臂 {in_a} 转向连接不一致:net 独有出口 {sorted(miss_n)},"
                           f"xodr 独有出口 {sorted(miss_x)}",
                    data={"arm": in_a, "net_only": sorted(miss_n), "xodr_only": sorted(miss_x)}))

    # 无法对比的路口
    unmatched_x = [j.name or j.id for j in xodr_map.junctions.values()
                   if not any(m.xodr_id == j.id for m in matches)]
    # net 侧:区域内且未被匹配;但道路端点"到达"的路口是场景边界断头
    # (裁剪边界,SUMO 车辆出界由联仿场景门控处理),不算拓扑缺失
    net_endpoints = []
    for info in xodr_map.by_road.values():
        net_endpoints.append((info["start"][0] - dx0, info["start"][1] - dy0))
        net_endpoints.append((info["end"][0] - dx0, info["end"][1] - dy0))
    unmatched_n = []
    edge_n = 0
    for j in net.junctions.values():
        if j.id in matched_net or not _junction_in_region(net, j.id, region):
            continue
        if any(math.dist((j.x, j.y), ep) < SCENE_EDGE_REACH_M for ep in net_endpoints):
            edge_n += 1
            continue
        unmatched_n.append(j.id)
    unmatched_n.sort()
    if unmatched_x:
        findings.append(Finding(
            junction="", method="", level="WARNING", kind="unmatched_junction",
            detail=f"xodr 中有 {len(unmatched_x)} 个路口在 net 中无对应:{unmatched_x[:20]}",
            data={"unmatched_xodr": unmatched_x}))
    if unmatched_n:
        findings.append(Finding(
            junction="", method="", level="WARNING", kind="unmatched_junction",
            detail=f"net 区域内有 {len(unmatched_n)} 个路口在 xodr 中无对应"
                   f"(场景未覆盖或 RoadRunner 合并/删除;另排除场景边界断头 {edge_n} 个)"
                   f":{unmatched_n[:20]}",
            data={"unmatched_net": unmatched_n, "scene_edge_excluded": edge_n}))

    level = max((f.level for f in findings), default="OK", key=lambda l: LEVELS[l])
    counts = {}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    msg = "全部一致。" if not findings else \
        "发现 " + ", ".join(f"{v} 处 {k}" for k, v in sorted(counts.items()))
    return CheckResult(level=level, message=msg, findings=findings,
                       data={"matched": len(matches), "unmatched_xodr": unmatched_x,
                             "unmatched_net": unmatched_n})


# ── 检查 C:官方工具 round-trip ───────────────────────────────────────────────
def check_environment(script_dir: str) -> tuple[bool, str, dict]:
    """服务器环境门禁:SUMO_HOME + netconvert + 官方 typ.xml 必须可用。

    统一经 toolchain_env 解析(优先级:环境变量 > config/toolchain.json > 自动探测);
    typ.xml 失败时返回全部候选路径并提示配置 toolchain.json。"""
    import toolchain_env
    env = {}
    sumo_home = toolchain_env.resolve_sumo_home()
    env["sumo_home"] = sumo_home or "(未设置)"
    if not sumo_home:
        return False, "SUMO_HOME 未设置(可 export SUMO_HOME,或在 config/toolchain.json 配置 sumo_home)。", env
    nc = toolchain_env.find_netconvert() or "netconvert"
    env["netconvert"] = nc
    if not os.path.isfile(nc):
        return False, f"netconvert 不存在: {nc}", env
    typ, candidates = toolchain_env.find_typ_file()
    env["typ_file"] = typ or ""
    env["typ_candidates"] = candidates
    if not typ:
        return (False,
                "官方 typ.xml(opendrive_netconvert.typ.xml)未找到,已搜索:\n"
                + "\n".join(f"  - {c}" for c in candidates)
                + "\n请在 config/toolchain.json 配置 sumo_toolkit_dir"
                  "(如 CARLA 源码的 Co-Simulation/Sumo 目录)后重试。",
                env)
    return True, "", env


def run_netconvert(netconvert_bin: str, xodr_path: str, typ_path: str,
                   keep_tmp: bool) -> tuple[int, str | None, list[str], str]:
    """用 CARLA 官方工具 netconvert_carla.py 的同款命令把 xodr 重转为 net.xml。
    返回 (rc, 转换产物路径|None, 警告列表, 错误输出尾部)。"""
    tmpdir = tempfile.mkdtemp(prefix="validate_rt_")
    base = os.path.splitext(os.path.basename(xodr_path))[0]
    out = os.path.join(tmpdir, base + ".net.xml")
    cmd = [netconvert_bin,
           "--opendrive", xodr_path,
           "--output-file", out,
           "--geometry.min-radius.fix",
           "--geometry.remove",
           "--opendrive.curve-resolution", "1",
           "--opendrive.import-all-lanes",
           "--type-files", typ_path,
           "--output.original-names",
           "--tls.discard-loaded", "true"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=NETCONVERT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        if not keep_tmp:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return 1, None, [], f"netconvert 超时(>{NETCONVERT_TIMEOUT_S}s)"
    except OSError as exc:
        if not keep_tmp:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return 1, None, [], f"netconvert 启动失败: {exc}"
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    warnings = [ln.strip() for ln in combined.splitlines()
                if ("Warning" in ln or "Error" in ln) and ln.strip()][:20]
    tail = "\n".join((proc.stderr or "").splitlines()[-20:])
    if proc.returncode != 0:
        if not keep_tmp:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return proc.returncode, None, warnings, tail
    if keep_tmp:
        print(f"round-trip 临时目录保留: {tmpdir}", file=sys.stderr)
    return 0, out, warnings, tail


def check_roundtrip(rt_net_path: str, xodr_map: XodrMap, net: NetTopology,
                    offset: tuple[float, float], lane_tol: int, bearing_tol: float,
                    match_radius: float, coverage_threshold: float,
                    netconvert_rc: int, netconvert_warnings: list[str],
                    netconvert_tail: str) -> CheckResult:
    if netconvert_rc != 0:
        return CheckResult(level="ERROR", message="netconvert 转换失败,联仿不可用。",
                           data={"netconvert_rc": netconvert_rc,
                                 "netconvert_tail": netconvert_tail[-2000:],
                                 "netconvert_warnings": netconvert_warnings})
    rt = load_net_topology(rt_net_path)
    if not rt.junctions:
        return CheckResult(level="ERROR", message="round-trip 转换结果为空路网。",
                           data={"netconvert_rc": netconvert_rc,
                                 "netconvert_warnings": netconvert_warnings})

    # 参考集 = 偏移补偿后的 xodr bbox 区域内的 net junction(排除 internal)
    dx, dy = offset
    region = region_in_net_frame(xodr_map.bbox, offset)
    ref_juncs = {jid: j for jid, j in net.junctions.items()
                 if _junction_in_region(net, jid, region)}
    ref_names = {j.id for j in ref_juncs.values()}

    # 匹配:id 桥接(xodr id ↔ name 桥)再几何(rt 坐标 + offset 补偿)
    rt_by_name: dict[str, str] = {}
    for xid, xj in xodr_map.junctions.items():
        if xj.name in ref_names:
            rt_by_name[xj.name] = xid
    matched_ids: set[str] = set()
    used_rt: set[str] = set()
    pairs: list[tuple[str, str, str, float]] = []  # (ref_id, rt_id, method, dist)
    for ref_id, rt_xid in rt_by_name.items():
        if rt_xid in rt.junctions:
            pairs.append((ref_id, rt_xid, "bridge", 0.0))
            matched_ids.add(ref_id)
            used_rt.add(rt_xid)
    geo_pairs = []
    for ref_id, rj in ref_juncs.items():
        if ref_id in matched_ids:
            continue
        for rt_id, rtj in rt.junctions.items():
            if rt_id in used_rt:
                continue
            d = math.hypot(rtj.x + dx - rj.x, rtj.y + dy - rj.y)
            if d <= match_radius:
                geo_pairs.append((d, ref_id, rt_id))
    for d, ref_id, rt_id in sorted(geo_pairs):
        if ref_id in matched_ids or rt_id in used_rt:
            continue
        pairs.append((ref_id, rt_id, "geo", d))
        matched_ids.add(ref_id)
        used_rt.add(rt_id)

    findings: list[Finding] = []
    type_mismatches: list[str] = []
    for ref_id, rt_id, method, dist in pairs:
        label = ref_id
        rtj = rt.junctions[rt_id]
        refj = net.junctions[ref_id]
        rt_arms = _net_arms(rt, rt_id)
        ref_arms_all = _net_arms(net, ref_id)
        # 参考 net 大于场景时,排除远端路口在场景区域外的边界手臂
        ref_arms = {eid: a for eid, a in ref_arms_all.items()
                    if _junction_in_region(net, net.edges[eid].from_j, region)}
        rt_out = _net_outgoing(rt, rt_id)
        ref_out_all = _net_outgoing(net, ref_id)
        ref_out = {}
        for in_e, outs in ref_out_all.items():
            if not _junction_in_region(net, net.edges[in_e].from_j, region):
                continue
            ref_out[in_e] = {o for o in outs
                             if _junction_in_region(net, net.edges[o].to_j, region)}
        arm_pairs = _greedy_match_by_bearing(
            [(a, ar["bearing"] or 0.0) for a, ar in rt_arms.items()],
            [(a, ar["bearing"] or 0.0) for a, ar in ref_arms.items()],
            bearing_tol)
        if len(rt_arms) != len(ref_arms):
            findings.append(Finding(
                junction=label, method=method, level="ERROR", kind="arm_count",
                detail=f"round-trip 手臂数 {len(rt_arms)} vs 参考 {len(ref_arms)}"
                       "——官方转换无法复现该路口拓扑",
                data={"rt_arms": len(rt_arms), "ref_arms": len(ref_arms)}))
        for ra, na in arm_pairs.items():
            if abs(rt_arms[ra]["in_lanes"] - ref_arms[na]["in_lanes"]) > lane_tol:
                findings.append(Finding(
                    junction=label, method=method, level="WARNING", kind="lane_count",
                    detail=f"臂 {ra} 入向车道数 round-trip={rt_arms[ra]['in_lanes']} vs "
                           f"参考={ref_arms[na]['in_lanes']}",
                    data={"arm": ra, "rt_lanes": rt_arms[ra]["in_lanes"],
                          "ref_lanes": ref_arms[na]["in_lanes"]}))
        for ra, na in arm_pairs.items():
            if na not in ref_out:
                continue
            x_outs = rt_out.get(ra, set())
            n_outs = ref_out[na]
            if not x_outs:
                findings.append(Finding(
                    junction=label, method=method, level="ERROR", kind="dead_end",
                    detail=f"臂 {ra} 在参考 net 有 {len(n_outs)} 个出口,round-trip 无任何连接",
                    data={"arm": ra, "ref_outgoing": sorted(n_outs)}))
        # junction 类型对比(round-trip 有 type,参考亦有)
        if refj.type != rtj.type:
            if refj.type == "traffic_light":
                type_mismatches.append(f"{label}: ref={refj.type} rt={rtj.type}(预期——"
                                       "官方工具丢弃 xodr 信号灯,联仿时由 CARLA 管线手动重建)")
                findings.append(Finding(
                    junction=label, method=method, level="WARNING", kind="junction_type",
                    detail=f"信号灯路口 {label} 在 round-trip 中为 {rtj.type}:预期差异,"
                           "CARLA 管线导入时会重建信号灯", data={}))
            else:
                type_mismatches.append(f"{label}: ref={refj.type} rt={rtj.type}")
                findings.append(Finding(
                    junction=label, method=method, level="WARNING", kind="junction_type",
                    detail=f"路口类型不一致:参考 {refj.type} vs round-trip {rtj.type}",
                    data={"ref_type": refj.type, "rt_type": rtj.type}))

    coverage = len(matched_ids) / len(ref_juncs) if ref_juncs else 0.0
    if not ref_juncs:
        findings.append(Finding(
            junction="", method="", level="WARNING", kind="coverage",
            detail="round-trip 参考区域无路口(场景与参考 net 无交集)——无法对比覆盖,请先检查对齐",
            data={"matched": 0, "reference": 0, "coverage": 0.0}))
    elif coverage < 0.5:
        findings.append(Finding(
            junction="", method="", level="WARNING", kind="coverage",
            detail=f"round-trip 覆盖明显不足:仅匹配 {len(matched_ids)}/{len(ref_juncs)} 个区域路口"
                   f"({coverage:.0%} < 50%)。官方 netconvert 的 OpenDRIVE 导入为有损转换"
                   "(转向连接缺失/路口位置偏移属已知限制,低覆盖是常见现象而非路网损坏);"
                   "联仿为 SUMO 主导(路由由 SUMO net.xml 决定),本项仅作诊断参考,不阻断联仿",
            data={"matched": len(matched_ids), "reference": len(ref_juncs),
                  "coverage": round(coverage, 3)}))
    elif coverage < coverage_threshold:
        findings.append(Finding(
            junction="", method="", level="WARNING", kind="coverage",
            detail=f"round-trip 覆盖 {len(matched_ids)}/{len(ref_juncs)}"
                   f"({coverage:.0%} < {coverage_threshold:.0%})——部分路口无法由官方工具复现,"
                   "属官方 OpenDRIVE 导入的有损性所致;SUMO 主导模式下仅作诊断参考,不阻断联仿",
            data={"matched": len(matched_ids), "reference": len(ref_juncs),
                  "coverage": round(coverage, 3)}))

    level = max((f.level for f in findings), default="OK", key=lambda l: LEVELS[l])
    msg = (f"netconvert rc=0,round-trip 覆盖 {len(matched_ids)}/{len(ref_juncs)}"
           f"({coverage:.0%})。") + ("全部一致。" if not findings else
                                     f"发现 {len(findings)} 处问题。")
    return CheckResult(level=level, message=msg, findings=findings,
                       data={"netconvert_rc": netconvert_rc,
                             "netconvert_warnings": netconvert_warnings,
                             "offset_compensated_m": {"dx": dx, "dy": dy},
                             "reference_junctions_in_region": len(ref_juncs),
                             "matched": len(matched_ids),
                             "coverage": round(coverage, 3),
                             "type_mismatches": type_mismatches})


# ── 报告与聚合 ───────────────────────────────────────────────────────────────
def overall_level(results: list[CheckResult]) -> str:
    return max((r.level for r in results), default="OK", key=lambda l: LEVELS[l])


def build_report(args, env: dict, xodr_path: str, net_path: str,
                 xodr_map: XodrMap, net: NetTopology,
                 res_a: CheckResult, res_b: CheckResult, res_c: CheckResult) -> dict:
    return {
        "schema_version": 1,
        "map_name": args.map_name,
        "files": {"xodr": xodr_path, "net": net_path},
        "env": env,
        "checks": {
            "A_alignment": {"level": res_a.level, "message": res_a.message,
                            "findings": [f.__dict__ for f in res_a.findings],
                            **res_a.data},
            "B_topology": {"level": res_b.level, "message": res_b.message,
                           "findings": [f.__dict__ for f in res_b.findings],
                           **res_b.data},
            "C_roundtrip": {"level": res_c.level, "message": res_c.message,
                            "findings": [f.__dict__ for f in res_c.findings],
                            **res_c.data},
        },
        "exit_code": EXIT_ERROR if overall_level([res_a, res_b, res_c]) == "ERROR"
        else EXIT_WARNING if overall_level([res_a, res_b, res_c]) == "WARNING" else EXIT_OK,
    }


def print_human_report(args, env: dict, xodr_path: str, net_path: str,
                       xodr_map: XodrMap, net: NetTopology,
                       res_a: CheckResult, res_b: CheckResult, res_c: CheckResult,
                       out=sys.stdout) -> None:
    print(f"validate.py — 地图联仿有效性校验: {args.map_name}", file=out)
    print(f"  xodr: {xodr_path}", file=out)
    print(f"  net : {net_path}  (junctions: {len(net.junctions)})", file=out)
    print(f"  env : SUMO_HOME={env.get('sumo_home')}  netconvert={env.get('netconvert')}", file=out)
    print(file=out)

    for key, title, res in (("A_alignment", "检查 A — 坐标对齐", res_a),
                            ("B_topology", "检查 B — 路口拓扑", res_b),
                            ("C_roundtrip", "检查 C — 官方工具 round-trip", res_c)):
        print(f"── {title} ──", file=out)
        if key == "A_alignment" and res.data.get("junction_deltas"):
            print(f"  {'路口':<10}{'SUMO x':>10}{'SUMO y':>10}{'xodr x':>10}{'xodr y':>10}"
                  f"{'dx':>9}{'dy':>9}{'|d|':>8}", file=out)
            for r in res.data["junction_deltas"]:
                conf = "" if r.get("n", 0) >= 3 else " (低置信)"
                dx, dy = r["delta_m"]
                print(f"  {r['junction']:<10}{r['sumo'][0]:>10.2f}{r['sumo'][1]:>10.2f}"
                      f"{r['xodr'][0]:>10.2f}{r['xodr'][1]:>10.2f}"
                      f"{dx:>9.2f}{dy:>9.2f}{r['dist_m']:>8.2f}{conf}", file=out)
            off = res.data.get("offset_m", {})
            spread = res.data.get("spread_m", {})
            print(f"  均值偏移: dx={off.get('dx', 0):+.2f}, dy={off.get('dy', 0):+.2f}"
                  f"  (散布 dx={spread.get('dx', 0):.2f}, dy={spread.get('dy', 0):.2f})", file=out)
        for f in res.findings:
            where = f"@{f.junction}" if f.junction else ""
            print(f"  {LEVEL_ICON[f.level]} [{f.kind}] {where} {f.detail}", file=out)
        sug = res.data.get("suggestion")
        if sug:
            print(f"  {sug}", file=out)
        print(f"  {LEVEL_ICON[res.level]} {title} → {res.level}: {res.message}", file=out)
        print(file=out)

    final = overall_level([res_a, res_b, res_c])
    print(f"总判定: {LEVEL_ICON[final]} {final}(退出码 "
          f"{EXIT_ERROR if final == 'ERROR' else EXIT_WARNING if final == 'WARNING' else EXIT_OK})",
          file=out)
    if final == "ERROR":
        print("  ❗ 建议修复后再启动联仿(SUMO 车辆将偏离道路或拓扑不匹配)。", file=out)
    elif final == "WARNING":
        print("  💡 存在警告项,联仿可运行但可能出现视觉/行为偏差,请人工确认。", file=out)


def resolve_map_paths(carla_dir: str, net_dir: str, name: str) -> tuple[str, str]:
    """按固定目录布局解析同名文件:
    xodr = <carla-dir>/<name>/<name>.xodr(RoadRunner 导出)
    net  = <net-dir>/<name>/<name>.clipped.net.xml(SUMO 裁剪子网)"""
    return (os.path.join(carla_dir, name, name + ".xodr"),
            os.path.join(net_dir, name, name + ".clipped.net.xml"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="地图联仿有效性校验:自动寻找同名 .xodr 与 .net.xml,检查坐标对齐、"
                    "路口拓扑与官方工具 round-trip 可行性。仅在服务器上运行(需 SUMO_HOME + netconvert)。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码: 0=OK  1=WARNING  2=ERROR  3=运行故障(环境/文件/解析/netconvert)",
    )
    parser.add_argument("map_name", help="地图名称(自动定位 <carla-dir>/<name>/<name>.xodr 与 "
                                         "<net-dir>/<name>/<name>.clipped.net.xml)")
    parser.add_argument("--carla-dir", default="",
                        help="CARLA 地图根目录,xodr 位于 <carla-dir>/<name>/<name>.xodr"
                             "(默认 config/toolchain.json maps_carla_dir,即 ../../data/maps/carla)")
    parser.add_argument("--net-dir", default="",
                        help="SUMO 裁剪子网目录,net 位于 <net-dir>/<name>/<name>.clipped.net.xml"
                             "(默认 config/toolchain.json maps_xodr_dir,即 ../../data/maps/xodr)")
    parser.add_argument("--threshold", type=float, default=3.0,
                        help="检查A 消息措辞分界(米,默认 3.0:< 此值为路口中心估计噪声,"
                             "≥ 此值为系统性偏移,两者均给 WARNING)")
    parser.add_argument("--error-threshold", type=float, default=10.0,
                        help="检查A WARNING/ERROR 分界(米,默认 10.0;≥ 此值判定联仿不可用)")
    parser.add_argument("--match-radius", type=float, default=5.0,
                        help="几何路口匹配半径(米,默认 5.0)")
    parser.add_argument("--lane-tolerance", type=int, default=1,
                        help="每臂 driving 车道数容差(默认 1,合并/拆分产物)")
    parser.add_argument("--bearing-tolerance", type=float, default=15.0,
                        help="臂-臂方位角配对容差(度,默认 15.0)")
    parser.add_argument("--coverage-threshold", type=float, default=0.9,
                        help="检查C round-trip 覆盖率阈值(默认 0.9;低于阈值给 WARNING,"
                             "低于 0.5 时警告措辞更明确)")
    parser.add_argument("--json", action="store_true",
                        help="机器报告写 stdout(人类文本写 stderr),退出码不变")
    parser.add_argument("--keep-tmp", action="store_true",
                        help="保留 netconvert 临时目录(调试用)")
    args = parser.parse_args(argv)

    # 地图目录统一经 toolchain_env 解析(CLI > config/toolchain.json > 默认 ../../data/maps/...)
    import toolchain_env
    args.carla_dir = toolchain_env.resolve_maps_carla_dir(args.carla_dir)
    args.net_dir = toolchain_env.resolve_maps_xodr_dir(args.net_dir)

    script_dir = os.path.dirname(os.path.realpath(__file__))

    # 1. 环境门禁(服务器专用)
    ok_env, err_env, env = check_environment(script_dir)
    if not ok_env:
        print("validate.py 仅在服务器上运行(需要 SUMO_HOME + netconvert + 官方 typ.xml)。",
              file=sys.stderr)
        print(err_env, file=sys.stderr)
        return EXIT_OPERATIONAL

    # 2. 文件定位
    xodr_path, net_path = resolve_map_paths(args.carla_dir, args.net_dir, args.map_name)
    if not (os.path.isfile(xodr_path) and os.path.isfile(net_path)):
        missing = [p for p in (xodr_path, net_path) if not os.path.isfile(p)]
        print("文件不存在(请放入固定目录后重试):", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        return EXIT_OPERATIONAL

    # 3. 解析
    try:
        xodr_map = load_xodr_topology(xodr_path)
    except (ET.ParseError, OSError) as exc:
        print(f"ERROR: 解析 xodr 失败: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    try:
        net = load_net_topology(net_path)
    except (ET.ParseError, OSError) as exc:
        print(f"ERROR: 解析 net.xml 失败: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    if not xodr_map.junctions or not net.junctions:
        print("ERROR: 任一侧路网为空,无法校验。", file=sys.stderr)
        return EXIT_OPERATIONAL

    # 4. 检查 A(先用零偏移匹配拿精确对应,求系统性偏移)
    matches0 = match_junctions(xodr_map, net, offset=(0.0, 0.0), radius=args.match_radius)
    res_a, offset = check_alignment(xodr_map, net, matches0,
                                    args.threshold, args.error_threshold)

    # 5. 检查 B(用补偿后的几何匹配)
    matches1 = match_junctions(xodr_map, net, offset=offset, radius=args.match_radius)
    res_b = check_topology(xodr_map, net, matches1, offset,
                           args.lane_tolerance, args.bearing_tolerance, xodr_map.bbox)

    # 6. 检查 C(官方 netconvert round-trip)
    rc, rt_net_path, warnings, tail = run_netconvert(env["netconvert"], xodr_path,
                                                     env["typ_file"], args.keep_tmp)
    if rt_net_path is not None:
        res_c = check_roundtrip(rt_net_path, xodr_map, net, offset,
                                args.lane_tolerance, args.bearing_tolerance,
                                args.match_radius, args.coverage_threshold,
                                rc, warnings, tail)
    else:
        res_c = CheckResult(level="ERROR", message="netconvert 转换失败,联仿不可用。",
                            data={"netconvert_rc": rc, "netconvert_warnings": warnings,
                                  "netconvert_tail": tail[-2000:]})

    # 7. 报告
    report = build_report(args, env, xodr_path, net_path, xodr_map, net,
                          res_a, res_b, res_c)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    print_human_report(args, env, xodr_path, net_path, xodr_map, net,
                       res_a, res_b, res_c, out=sys.stderr if args.json else sys.stdout)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
