import re
import xml.etree.ElementTree as ET

root = ET.parse(r"D:\download\左侧数据.svg").getroot()

for el in root.iter():
    tag = el.tag.split("}")[-1]
    if tag != "path":
        continue
    d = el.attrib.get("d", "")
    if len(d) > 200 and ("851" in d or "870" in d or "543" in d):
        print("PATH len", len(d))
        print(d[:500])
        print("...")
        a = {k.split("}")[-1]: v for k, v in el.attrib.items()}
        print("fill:", a.get("fill", "")[:60])
        print("stroke:", a.get("stroke", ""))
        print()
