import carla
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
osm_path = PROJECT_ROOT / "data" / "maps" / "osm" / "xiongan_test.osm"
xodr_path = PROJECT_ROOT / "data" / "maps" / "carla" / "xiongan_test.xodr"

osm_data = osm_path.read_text()

settings = carla.Osm2OdrSettings()

# 需要转换的道路类型
settings.set_osm_way_types([
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "living_street",
    "unclassified",
    "residential",
    "service"
])

# 根据 OSM 生成信号灯
settings.generate_traffic_lights = True

xodr_data = carla.Osm2Odr.convert(osm_data, settings)
xodr_path.write_text(xodr_data)

print(f"保存至: {xodr_path.resolve()}")
