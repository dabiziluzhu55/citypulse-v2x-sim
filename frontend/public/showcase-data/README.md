# Showcase source assets

`demo_2.roads.wgs84.geojson` is a frontend compatibility asset generated from
`data/maps/sumo/generated/network/TotalMap_20.signals.net.xml` with
`simulation/utils/convert_sumo_road_network.py` at a 600 m radius. The backend
no longer publishes this per-intersection GeoJSON, while the frontend showcase
generators still need the same 16-road geometry for deterministic facilities,
vegetation, land cover, and static road tiles.

Regenerate it from the repository root with:

```powershell
$env:PYTHONPATH='.'
python simulation/utils/convert_sumo_road_network.py `
  --net-file data/maps/sumo/generated/network/TotalMap_20.signals.net.xml `
  --intersection-id demo_2 `
  --center-lon 116.126756 `
  --center-lat 38.99115 `
  --radius-m 600 `
  --output frontend/public/showcase-data/demo_2.roads.wgs84.geojson
```
