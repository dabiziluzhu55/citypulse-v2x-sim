import re
import xml.etree.ElementTree as ET
from pathlib import Path

FILES = [
    r"D:\download\底部图标.svg",
    r"D:\download\底部中间.svg",
    r"D:\download\头部标题.svg",
    r"D:\download\左下.svg",
    r"D:\download\左上.svg",
    r"D:\download\右边框.svg",
    r"D:\download\左边框.svg",
]


def parse_matrix(transform):
    if not transform or "matrix" not in transform:
        return None
    nums = [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", transform)]
    if len(nums) != 6:
        return None
    return nums


def rect_box(el):
    a = {k.split("}")[-1]: v for k, v in el.attrib.items()}
    w = float(a.get("width", 0) or 0)
    h = float(a.get("height", 0) or 0)
    x = float(a.get("x", 0) or 0)
    y = float(a.get("y", 0) or 0)
    t = a.get("transform", "")
    m = parse_matrix(t)
    if m:
        a1, b1, c1, d1, e, f = m
        if a1 == -1 and d1 == 1:
            x = e - w
            y = f
    elif t.startswith("translate"):
        tx, ty = [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", t)]
        x, y = tx, ty
    return x, y, w, h


for path in FILES:
    print("=" * 60)
    print(Path(path).name)
    if not Path(path).exists():
        print("MISSING")
        continue
    root = ET.parse(path).getroot()
    print("viewBox:", root.get("viewBox"), "size:", root.get("width"), root.get("height"))
    rects = []
    circles = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "rect":
            x, y, w, h = rect_box(el)
            rects.append((y, x, w, h))
        elif tag == "circle":
            a = {k.split("}")[-1]: v for k, v in el.attrib.items()}
            circles.append((float(a.get("cy", 0)), float(a.get("cx", 0)), float(a.get("r", 0))))
    rects.sort()
    for y, x, w, h in rects[:20]:
        print(f"  rect y={y:6.1f} x={x:6.1f} w={w:6.1f} h={h:6.1f}")
    if len(rects) > 20:
        print(f"  ... {len(rects)} rects total")
    for cy, cx, r in sorted(circles)[:10]:
        print(f"  circle cx={cx:.1f} cy={cy:.1f} r={r:.1f}")
