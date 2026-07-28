import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { projectBd09ToWebMercator, wgs84ToBd09 } from '../src/mapv/sceneCoordinates.ts'
import { cropPolylineToRadius, validateIntersectionManifest } from './realistic-intersection-core.mjs'
import {
  buildIntersectionApproachGeometry,
  CROSSWALK_FIRST_CENTER_METERS,
  CROSSWALK_STRIPE_COUNT,
  CROSSWALK_STRIPE_WIDTH_METERS,
  SIGNAL_POLE_LATERAL_CLEARANCE_METERS,
  SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS,
  signalPoleBase,
  STOP_LINE_CENTER_OFFSET_METERS,
} from '../src/mapv/realistic/intersectionApproachGeometry.ts'
import { visualLanePoints } from '../src/mapv/realistic/intersectionRoadGeometry.ts'
import { parseIntersectionEnvironmentManifest } from '../src/mapv/realistic/intersectionEnvironmentManifest.ts'

function pointToSegmentDistance(point, start, end) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const denominator = dx * dx + dy * dy
  const ratio = denominator === 0 ? 0 : Math.max(0, Math.min(1,
    ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator,
  ))
  return Math.hypot(point[0] - start[0] - dx * ratio, point[1] - start[1] - dy * ratio)
}

test('clips a lane shape at the exact preview radius', () => {
  const clipped = cropPolylineToRadius([[-200, 0], [-80, 0], [0, 0]], 140)
  assert.deepEqual(clipped, [[-140, 0], [-80, 0], [0, 0]])
})

test('catalog contains 20 projection-correct realistic intersections', async () => {
  const catalog = JSON.parse(await readFile(
    new URL('../public/intersections/v3/catalog.json', import.meta.url),
    'utf8',
  ))
  assert.equal(catalog.schemaVersion, 3)
  assert.equal(catalog.intersections.length, 20)
  assert.deepEqual(
    catalog.intersections.map((item) => item.intersectionId),
    Array.from({ length: 20 }, (_, index) => `demo_${index + 1}`),
  )
  for (const entry of catalog.intersections) {
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/${entry.intersectionId}/manifest.json`, import.meta.url),
      'utf8',
    ))
    assert.deepEqual(validateIntersectionManifest(manifest), [], entry.intersectionId)
    assert.equal(manifest.intersectionId, entry.intersectionId)
    assert.ok(manifest.horizontalScale > 1.28 && manifest.horizontalScale < 1.30)
    assert.ok(manifest.connections.length > 0)
    assert.ok(manifest.edges.some((edge) => edge.incoming))
    assert.ok(manifest.edges.some((edge) => !edge.incoming))
  }
})

test('all realistic intersections have a matching environment bundle', async () => {
  for (let index = 1; index <= 20; index += 1) {
    const id = `demo_${index}`
    const directory = new URL(`../public/intersections/v3/${id}/`, import.meta.url)
    const [environmentSource, facilitiesSource, vegetationSource] = await Promise.all([
      readFile(new URL('environment.json', directory), 'utf8'),
      readFile(new URL('facilities.json', directory), 'utf8'),
      readFile(new URL('vegetation.json', directory), 'utf8'),
    ])
    const environment = parseIntersectionEnvironmentManifest(JSON.parse(environmentSource), id)
    const facilities = JSON.parse(facilitiesSource)
    const vegetation = JSON.parse(vegetationSource)
    assert.equal(environment.intersectionId, id)
    assert.equal(facilities.intersectionId, id)
    assert.ok(facilities.lamps.length > 0, `${id} requires roadside lamps`)
    assert.ok(vegetation.items.length > 0, `${id} requires vegetation`)
  }
})

test('anchors stop lines at lane ends and places crosswalks after them', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  for (const edge of manifest.edges.filter((candidate) => candidate.incoming)) {
    const approach = buildIntersectionApproachGeometry(edge, manifest.horizontalScale, manifest.edges)
    assert.ok(approach)
    for (const sample of approach.laneSamples) {
      const laneEnd = visualLanePoints(sample.lane).at(-1)
      const distanceMeters = Math.hypot(
        sample.point[0] - laneEnd[0],
        sample.point[1] - laneEnd[1],
      ) / manifest.horizontalScale
      assert.ok(Math.abs(distanceMeters - STOP_LINE_CENTER_OFFSET_METERS) < 0.01)
    }
    const firstCrosswalk = approach.crosswalkCenters[0]
    const along = (
      (firstCrosswalk[0] - approach.stopLineCenter[0]) * approach.tangent[0]
      + (firstCrosswalk[1] - approach.stopLineCenter[1]) * approach.tangent[1]
    ) / manifest.horizontalScale
    assert.ok(Math.abs(along - CROSSWALK_FIRST_CENTER_METERS) < 0.01)
    assert.ok(along > 0)
    assert.equal(approach.crosswalkCenters.length, CROSSWALK_STRIPE_COUNT)
    assert.ok(Math.abs(
      along - CROSSWALK_STRIPE_WIDTH_METERS / 2 - 1.2
    ) < 0.01)
  }
})

test('demo_2 rebuilds non-overlapping uniform lane cross sections with lane semantics', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  for (const edge of manifest.edges) {
    assert.ok(edge.centerline.length >= 20, `${edge.id} requires a shared centerline`)
    assert.ok(Math.abs(
      edge.roadWidth - edge.lanes.reduce((sum, lane) => sum + lane.width, 0)
    ) < 0.01)
    const endpoints = edge.lanes.map((lane) => (
      edge.incoming ? lane.renderPoints.at(-1) : lane.renderPoints[0]
    ))
    for (let index = 0; index < endpoints.length - 1; index += 1) {
      const actual = Math.hypot(
        endpoints[index + 1][0] - endpoints[index][0],
        endpoints[index + 1][1] - endpoints[index][1],
      )
      const expected = (edge.lanes[index].width + edge.lanes[index + 1].width) / 2
      assert.ok(Math.abs(actual - expected) < 0.01, `${edge.id} lane spacing is not uniform`)
    }
  }
  for (const edgeId of ['-56734', '-56736', '-57228', '-57229']) {
    const edge = manifest.edges.find((candidate) => candidate.id === edgeId)
    assert.equal(edge.lanes[0].kind, 'bicycle')
    assert.equal(edge.lanes[0].widthMeters, 1)
    assert.ok(edge.lanes.slice(1).every((lane) => lane.kind === 'driving'))
  }
})

test('crosswalks span paired carriageways without fixed lateral overhang', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  for (const edge of manifest.edges.filter((candidate) => candidate.incoming)) {
    const approach = buildIntersectionApproachGeometry(edge, manifest.horizontalScale, manifest.edges)
    assert.ok(approach)
    assert.ok(approach.crosswalkHalfWidth >= approach.halfWidth)
    const pole = signalPoleBase(approach, manifest.horizontalScale)
    const poleProjection = pole[0] * approach.normal[0] + pole[1] * approach.normal[1]
    const lateralClearance = Math.abs(poleProjection - approach.outerBoundaryProjection)
      / manifest.horizontalScale
    const longitudinalSetback = -(
      (pole[0] - approach.stopLineCenter[0]) * approach.tangent[0]
      + (pole[1] - approach.stopLineCenter[1]) * approach.tangent[1]
    ) / manifest.horizontalScale
    assert.ok(Math.abs(lateralClearance - SIGNAL_POLE_LATERAL_CLEARANCE_METERS) < 0.01)
    assert.ok(Math.abs(longitudinalSetback - SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS) < 0.01)
    assert.ok(lateralClearance >= 0.75)
  }
})

test('demo_2 lanes align with the authoritative WGS84 road export', async () => {
  const [manifestSource, roadsSource, mappingSource] = await Promise.all([
    readFile(new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url), 'utf8'),
    readFile(new URL('../../data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson', import.meta.url), 'utf8'),
    readFile(new URL('../../data/maps/sumo/TotalMap_20.intersections.json', import.meta.url), 'utf8'),
  ])
  const manifest = JSON.parse(manifestSource)
  const roads = JSON.parse(roadsSource)
  const mapping = JSON.parse(mappingSource)
  assert.ok(Math.abs(manifest.origin.longitude - mapping.demo_2.junction_lon) < 1e-10)
  assert.ok(Math.abs(manifest.origin.latitude - mapping.demo_2.junction_lat) < 1e-10)

  const origin = manifest.origin.webMercator
  let maximumDistance = 0
  for (const edge of manifest.edges) {
    const feature = roads.features.find((item) => String(item.properties.edge_id) === edge.id)
    assert.ok(feature, `missing GeoJSON edge ${edge.id}`)
    const line = feature.geometry.coordinates.map((coordinate) => {
      const projected = projectBd09ToWebMercator(wgs84ToBd09(...coordinate))
      return [projected[0] - origin[0], projected[1] - origin[1]]
    })
    for (const lane of edge.lanes) {
      for (const point of lane.points) {
        const distance = Math.min(...line.slice(0, -1).map((start, index) => (
          pointToSegmentDistance(point, start, line[index + 1])
        )))
        maximumDistance = Math.max(maximumDistance, distance)
      }
    }
  }
  assert.ok(maximumDistance < 8, `lane/road alignment error is ${maximumDistance.toFixed(3)}m`)
})

test('demo_2 keeps the exact TLS link contract used by the simulator', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  assert.equal(manifest.tlsIds[0], '317')
  assert.deepEqual(
    [...new Set(manifest.connections.map((item) => item.linkIndex))].sort((a, b) => a - b),
    Array.from({ length: 15 }, (_, index) => index),
  )
  assert.equal(manifest.phaseTemplates['1']['317'].green, 'GGGrrggrrrgGgGG')
})
