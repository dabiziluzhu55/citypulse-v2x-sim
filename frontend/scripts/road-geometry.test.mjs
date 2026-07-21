import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { buildDetailedRoadData } from '../src/mapv/roadGeometry.ts'
import {
  projectSimulationCoordinateToBaiduMap,
  projectSimulationCoordinateToBaiduXiongan,
  placeBaiduCameraTarget,
  resolveSimulationCoordinateProjector,
  DEMO_2_SOURCE_CENTER_BD09,
  XIONGAN_SCENE_ANCHOR_BD09,
} from '../src/mapv/sceneCoordinates.ts'

const realGeoJson = JSON.parse(readFileSync(
  new URL('../../data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson', import.meta.url),
  'utf8',
))

function response(features, metadata = {}) {
  return {
    intersection_id: 'demo_2',
    center: { longitude: 116.126756, latitude: 38.99115 },
    radius_m: 600,
    bounds: { west: 116.12, south: 38.98, east: 116.14, north: 39 },
    geojson: { type: 'FeatureCollection', metadata, features },
  }
}

function straightRoad(laneCount = 3) {
  return {
    type: 'Feature',
    properties: {
      edge_id: 'straight',
      lane_count: laneCount,
      width_m: laneCount * 3.35,
      speed: 22.22,
      priority: 2,
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [116.125, 38.991],
        [116.128, 38.991],
      ],
    },
  }
}

function assertClosedFinitePolygons(features) {
  for (const feature of features) {
    const ring = feature.geometry.coordinates[0]
    assert.ok(ring.length >= 4)
    assert.deepEqual(ring[0], ring.at(-1))
    assert.ok(ring.flat().every(Number.isFinite))
  }
}

test('a three-lane SUMO centerline becomes stable layered road geometry', () => {
  const data = buildDetailedRoadData(response([straightRoad()]))

  assert.equal(data.shoulders.length, 1)
  assert.equal(data.mainSurfaces.length, 1)
  assert.equal(data.secondarySurfaces.length, 0)
  assert.equal(data.outerBoundaries.length, 2)
  assert.equal(data.laneDividers.length, 2)
  assert.equal(data.medians.length, 0)
  assertClosedFinitePolygons([...data.shoulders, ...data.mainSurfaces])
  const longitudes = data.mainSurfaces[0].geometry.coordinates[0].map((point) => point[0])
  const [expectedLongitude] = projectSimulationCoordinateToBaiduMap([116.1265, 38.991])
  assert.ok(Math.abs(longitudes.reduce((sum, value) => sum + value, 0) / longitudes.length - expectedLongitude) < 0.005)
})

test('real SUMO GeoJSON produces lanes, paired medians, and junction markings', () => {
  const data = buildDetailedRoadData(response(realGeoJson.features, realGeoJson.metadata))

  assert.equal(data.mainSurfaces.length + data.secondarySurfaces.length, 15)
  assert.equal(data.outerBoundaries.length, 30)
  assert.equal(data.laneDividers.length, 13)
  assert.ok(data.medians.length >= 3)
  assert.ok(data.junctionSurfaces.length >= 1)
  assert.ok(data.stopLines.length >= 3)
  assert.ok(data.crosswalkStripes.length >= 12)
  assertClosedFinitePolygons([
    ...data.shoulders,
    ...data.mainSurfaces,
    ...data.secondarySurfaces,
    ...data.junctionSurfaces,
    ...data.stopLines,
    ...data.crosswalkStripes,
  ])
})

test('road geometry is deterministic when backend feature order changes', () => {
  const original = buildDetailedRoadData(response(realGeoJson.features, realGeoJson.metadata))
  const reversed = buildDetailedRoadData(response([...realGeoJson.features].reverse(), realGeoJson.metadata))

  assert.deepEqual(reversed, original)
})

test('road geometry uses the supplied scene projector', () => {
  const projector = ([longitude, latitude, height]) => [
    longitude + 1,
    latitude + 2,
    height,
  ]
  const data = buildDetailedRoadData(response([straightRoad()]), projector)
  const points = data.mainSurfaces[0].geometry.coordinates[0]

  assert.ok(points.every(([longitude]) => longitude > 117))
  assert.ok(points.every(([, latitude]) => latitude > 40))
})

test('scene placement resolves only the explicit Xiongan demo mode', () => {
  assert.equal(resolveSimulationCoordinateProjector('xiongan-demo'), projectSimulationCoordinateToBaiduXiongan)
  assert.equal(resolveSimulationCoordinateProjector('actual'), projectSimulationCoordinateToBaiduMap)
  assert.equal(resolveSimulationCoordinateProjector(undefined), projectSimulationCoordinateToBaiduMap)
})

test('camera targets are translated once in Xiongan demo mode', () => {
  const translated = placeBaiduCameraTarget([...DEMO_2_SOURCE_CENTER_BD09, 0], 'xiongan-demo')
  assert.ok(
    Math.hypot(
      translated[0] - XIONGAN_SCENE_ANCHOR_BD09[0],
      translated[1] - XIONGAN_SCENE_ANCHOR_BD09[1],
    ) < 1e-12,
  )
  assert.deepEqual(
    placeBaiduCameraTarget([...XIONGAN_SCENE_ANCHOR_BD09, 0], 'xiongan-demo'),
    [...XIONGAN_SCENE_ANCHOR_BD09, 0],
  )
  assert.deepEqual(
    placeBaiduCameraTarget([...DEMO_2_SOURCE_CENTER_BD09, 0], 'actual'),
    [...DEMO_2_SOURCE_CENTER_BD09, 0],
  )
})
