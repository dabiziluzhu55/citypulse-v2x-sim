import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  junctionSurfacesToPoints,
  projectFeatureCollection,
} from '../src/mapv/showcaseLayers/showcaseLayerData.ts'
import { buildDetailedRoadData } from '../src/mapv/roadGeometry.ts'
import { BAIDU_DARK_BASE_STYLE } from '../src/mapv/baiduDarkStyle.ts'
import { extractOsmLandcover } from './generate-showcase-landcover.mjs'

const roadGeoJson = JSON.parse(await readFile(
  new URL('../../data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson', import.meta.url),
  'utf8',
))

const response = (features = roadGeoJson.features) => ({
  intersection_id: 'demo_2',
  center: { longitude: 116.126756, latitude: 38.99115 },
  radius_m: 600,
  bounds: { west: 116.1, south: 38.9, east: 116.2, north: 39.1 },
  geojson: { ...roadGeoJson, features },
})

test('keeps Baidu roads, green, and water visible while native buildings stay hidden', () => {
  const styleByFeature = new Map(BAIDU_DARK_BASE_STYLE.map((entry) => [entry.featureType, entry]))
  for (const featureType of ['water', 'green', 'road']) {
    assert.equal(styleByFeature.get(featureType)?.stylers.visibility, 'on')
  }
  assert.equal(styleByFeature.get('building')?.stylers.visibility, 'off')
})

test('projects every coordinate depth while preserving properties', () => {
  const source = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: { name: 'water' },
        geometry: {
          type: 'Polygon',
          coordinates: [[
            [116, 39, 2],
            [117, 39, 2],
            [116, 39, 2],
          ]],
        },
      },
    ],
  }

  const result = projectFeatureCollection(source, ([longitude, latitude, height]) => [
    longitude + 0.01,
    latitude + 0.02,
    height,
  ])

  assert.deepEqual(result.features[0].properties, { name: 'water' })
  assert.deepEqual(result.features[0].geometry.coordinates[0][0], [116.01, 39.02, 2])
})

test('rejects malformed GeoJSON before it reaches mapv-three', () => {
  assert.throws(
    () => projectFeatureCollection({ type: 'Feature', features: [] }, (coordinate) => [...coordinate]),
    /FeatureCollection/,
  )
})

test('derives stable junction points from SUMO road surfaces', () => {
  const original = buildDetailedRoadData(response(), (coordinate) => [...coordinate])
  const reversed = buildDetailedRoadData(
    response([...roadGeoJson.features].reverse()),
    (coordinate) => [...coordinate],
  )

  const originalPoints = junctionSurfacesToPoints(original.junctionSurfaces)
  const reversedPoints = junctionSurfacesToPoints(reversed.junctionSurfaces)

  assert.ok(originalPoints.length > 0)
  assert.deepEqual(originalPoints, reversedPoints)
  assert.equal(originalPoints[0].geometry.type, 'Point')
  assert.equal(originalPoints[0].properties.model_type, 'junction-marker')
})

test('caps repeated model instances at 500 points', () => {
  const surfaces = Array.from({ length: 501 }, (_, index) => ({
    type: 'Feature',
    properties: { junction_index: index },
    geometry: {
      type: 'Polygon',
      coordinates: [[
        [index, 0, 0],
        [index + 1, 0, 0],
        [index + 1, 1, 0],
        [index, 0, 0],
      ]],
    },
  }))

  assert.equal(junctionSurfacesToPoints(surfaces).length, 500)
})

test('extracts stable closed OSM green and water polygons', () => {
  const osm = `<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="1" lon="1" />
  <node id="2" lat="1" lon="2" />
  <node id="3" lat="2" lon="2" />
  <node id="4" lat="2" lon="1" />
  <node id="5" lat="3" lon="3" />
  <node id="6" lat="3" lon="4" />
  <node id="7" lat="4" lon="4" />
  <node id="8" lat="4" lon="3" />
  <node id="9" lat="5" lon="5" />
  <node id="10" lat="5" lon="6" />
  <node id="11" lat="6" lon="6" />
  <node id="12" lat="6" lon="5" />
  <node id="13" lat="7" lon="1" />
  <node id="14" lat="7" lon="2" />
  <node id="15" lat="8" lon="2" />
  <node id="16" lat="8" lon="1" />
  <node id="17" lat="7" lon="3" />
  <node id="18" lat="7" lon="4" />
  <node id="19" lat="8" lon="4" />
  <node id="20" lat="8" lon="3" />
  <node id="21" lat="7" lon="5" />
  <node id="22" lat="7" lon="6" />
  <node id="23" lat="8" lon="6" />
  <node id="24" lat="8" lon="5" />
  <way id="20"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="leisure" v="park"/></way>
  <way id="10"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/><tag k="natural" v="water"/></way>
  <way id="30"><nd ref="1"/><nd ref="2"/><tag k="waterway" v="stream"/></way>
  <way id="5"><nd ref="9"/><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="9"/><tag k="landuse" v="residential"/></way>
  <way id="40"><nd ref="13"/><nd ref="14"/><nd ref="15"/><nd ref="16"/><nd ref="13"/><tag k="building" v="office"/><tag k="height" v="21 m"/></way>
  <way id="41"><nd ref="17"/><nd ref="18"/><nd ref="19"/><nd ref="20"/><nd ref="17"/><tag k="building" v="apartments"/><tag k="building:levels" v="4"/></way>
  <way id="42"><nd ref="21"/><nd ref="22"/><nd ref="23"/><nd ref="24"/><nd ref="21"/><tag k="building" v="yes"/></way>
</osm>`

  const result = extractOsmLandcover(osm, { west: 0, south: 0, east: 10, north: 10 })

  assert.deepEqual(result.green.features.map((feature) => feature.properties.osm_id), ['20'])
  assert.deepEqual(result.water.features.map((feature) => feature.properties.osm_id), ['10'])
  assert.deepEqual(result.urban.features.map((feature) => feature.properties.osm_id), ['5'])
  assert.deepEqual(result.buildings.features.map((feature) => feature.properties.osm_id), ['40', '41', '42'])
  assert.equal(result.buildings.features[0].properties.height, 21)
  assert.equal(result.buildings.features[1].properties.height, 12)
  assert.equal(result.buildings.features[2].properties.height, 15)
  assert.equal(result.buildings.features[2].properties.class, 'building')
  assert.equal(result.green.features[0].geometry.type, 'Polygon')
  assert.equal(result.water.features[0].geometry.coordinates[0].length, 5)
})
