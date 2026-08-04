import re
import xml.etree.ElementTree as ET
from pathlib import Path

for name in ["左上.svg", "左下.svg", "头部标题.svg", "底部图标.svg"]:
    path = Path(r"D:\download") / name
    root = ET.parse(path).getroot()
    print("=" * 50, name)
    print("viewBox:", root.get("viewBox"))
    counts = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        counts[tag] = counts.get(tag, 0) + 1
    print("elements:", counts)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "image":
            a = {k.split("}")[-1]: v for k, v in el.attrib.items()}
            print("image", a.get("href", a.get("xlink:href", ""))[:80], a.get("width"), a.get("height"))
