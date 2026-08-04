import re
import xml.etree.ElementTree as ET

tree = ET.parse(r"D:\download\右侧数据.svg")
root = tree.getroot()


def parse_matrix(transform):
    if not transform or "matrix" not in transform:
        return None
    nums = [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", transform)]
    if len(nums) != 6:
        return None
    a, b, c, d, e, f = nums
    return a, b, c, d, e, f


def rect_box(el):
    a = {k.split("}")[-1]: v for k, v in el.attrib.items()}
    w = float(a.get("width", 0))
    h = float(a.get("height", 0))
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


rows = []
for el in root.iter():
    if el.tag.split("}")[-1] != "rect":
        continue
    x, y, w, h = rect_box(el)
    rows.append((y, x, w, h))

rows.sort()
for y, x, w, h in rows:
    print(f"y={y:6.1f} x={x:6.1f} w={w:6.1f} h={h:6.1f}")
