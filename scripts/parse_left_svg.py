import xml.etree.ElementTree as ET

tree = ET.parse(r"D:\download\左侧数据.svg")
root = tree.getroot()


def attrs(el):
    return {k.split("}")[-1]: v for k, v in el.attrib.items()}


def walk(el, depth=0):
    tag = el.tag.split("}")[-1]
    a = attrs(el)
    extra = ""
    if tag == "rect":
        extra = " rect(%sx%s)" % (a.get("width"), a.get("height"))
    if tag in ("g", "rect") or "transform" in a:
        t = a.get("transform", "")
        if t or tag == "rect":
            print("  " * depth + tag, t[:60] if t else "", extra)
    for ch in el:
        walk(ch, depth + 1)


walk(root)
