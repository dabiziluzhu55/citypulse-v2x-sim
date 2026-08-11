import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { projectBd09ToWebMercator, wgs84ToBd09 } from '../src/mapv/sceneCoordinates.ts'
import { cropPolylineToRadius, validateIntersectionManifest } from './realistic-intersection-core.mjs'
import {
  buildIntersectionApproachGeometry,
  buildCollisionFreeIntersectionApproaches,
  crosswalksOverlap,
  CROSSWALK_DEPTH_METERS,
  CROSSWALK_FIRST_CENTER_METERS,
  CROSSWALK_STRIPE_WIDTH_METERS,
  SIGNAL_POLE_LATERAL_CLEARANCE_METERS,
  SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS,
  signalPoleBase,
  STOP_LINE_CENTER_OFFSET_METERS,
} from '../src/mapv/realistic/intersectionApproachGeometry.ts'
import { visualLanePoints } from '../src/mapv/realistic/intersectionRoadGeometry.ts'
import { parseIntersectionEnvironmentManifest } from '../src/mapv/realistic/intersectionEnvironmentManifest.ts'
import { sumoHeadingTransformIsValid } from '../src/mapv/sumoHeadingTransform.ts'

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
  const [catalogSource, sumoSource] = await Promise.all([
    readFile(new URL('../public/intersections/v3/catalog.json', import.meta.url), 'utf8'),
    readFile(new URL('../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml', import.meta.url)),
  ])
  const catalog = JSON.parse(catalogSource)
  const sourceSha256 = createHash('sha256').update(sumoSource).digest('hex')
  assert.equal(catalog.schemaVersion, 3)
  assert.equal(catalog.sourceSha256, sourceSha256)
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
    assert.equal(sumoHeadingTransformIsValid(entry.sumoHeadingTransform), true)
    assert.deepEqual(manifest.sumoHeadingTransform, entry.sumoHeadingTransform)
    assert.equal(manifest.sumoHeadingTransform.sourceSha256, sourceSha256)
    assert.equal(manifest.intersectionId, entry.intersectionId)
    assert.ok(manifest.horizontalScale > 1.28 && manifest.horizontalScale < 1.30)
    assert.ok(manifest.connections.length > 0)
    assert.ok(manifest.roadJoints?.length > 0, `${entry.intersectionId} requires topology road joints`)
    assert.ok(manifest.roadJoints.some((joint) => joint.junctionId === manifest.junctionId))
    assert.ok(manifest.roadJoints.every((joint) => joint.overlapMeters >= 0.5))
    assert.equal(manifest.radiusMeters, 520)
    const maximumRoadRadius = Math.max(...manifest.edges.flatMap((edge) => (
      edge.lanes.flatMap((lane) => lane.points.map((point) => Math.hypot(...point)))
    ))) / manifest.horizontalScale
    assert.ok(maximumRoadRadius >= 130, `${entry.intersectionId} road coverage is too short`)
    assert.ok(manifest.edges.some((edge) => edge.incoming))
    assert.ok(manifest.edges.some((edge) => !edge.incoming))
  }
})

test('targeted rebuilt intersections match the current SUMO source and repair authoritative demo_4 gaps', async () => {
  const [source, ...manifestSources] = await Promise.all([
    readFile(new URL('../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml', import.meta.url)),
    ...[4, 5, 6, 8, 9].map((index) => readFile(
      new URL(`../public/intersections/v3/demo_${index}/manifest.json`, import.meta.url),
      'utf8',
    )),
  ])
  const sourceSha256 = createHash('sha256').update(source).digest('hex')
  const manifests = manifestSources.map(JSON.parse)
  const [demo4] = manifests
  manifests.forEach((manifest) => assert.equal(manifest.sourceSha256, sourceSha256))

  const repairedPairs = [
    ['4463', '-56733', '-56735'],
    ['4463', '-56735', '-57184'],
    ['4484', '-57185', '-57232'],
    ['4484', '-57230', '-57232'],
  ]
  for (const [junctionId, ...edgeIds] of repairedPairs) {
    assert.ok(demo4.roadJoints.some((joint) => (
      joint.junctionId === junctionId
      && joint.source === 'sumo_junction_shape'
      && edgeIds.every((edgeId) => joint.connectedEdgeIds.includes(edgeId))
    )), `${junctionId} ${edgeIds.join('/')} must have an authoritative joint`)
  }
})

test('all realistic intersections have a matching environment bundle', async () => {
  for (let index = 1; index <= 20; index += 1) {
    const id = `demo_${index}`
    const directory = new URL(`../public/intersections/v3/${id}/`, import.meta.url)
    const [environmentSource, facilitiesSource, vegetationSource, greenSource, waterSource, buildingSource] = await Promise.all([
      readFile(new URL('environment.json', directory), 'utf8'),
      readFile(new URL('facilities.json', directory), 'utf8'),
      readFile(new URL('vegetation.json', directory), 'utf8'),
      readFile(new URL('green.geojson', directory), 'utf8'),
      readFile(new URL('water.geojson', directory), 'utf8'),
      readFile(new URL(`../public/3dtiles/intersections/${id}/manifest.json`, import.meta.url), 'utf8'),
    ])
    const environment = parseIntersectionEnvironmentManifest(JSON.parse(environmentSource), id)
    const facilities = JSON.parse(facilitiesSource)
    const vegetation = JSON.parse(vegetationSource)
    const green = JSON.parse(greenSource)
    const water = JSON.parse(waterSource)
    const buildings = JSON.parse(buildingSource)
    assert.equal(environment.intersectionId, id)
    assert.equal(facilities.intersectionId, id)
    assert.ok(facilities.lamps.length > 0, `${id} requires roadside lamps`)
    assert.equal(vegetation.items.length, 0, `${id} procedural vegetation must stay disabled`)
    assert.equal(environment.streetlight.modelUrl, '/assets/roadside/streetlight.glb')
    assert.equal(environment.streetlight.modelYawDegrees, 180)
    assert.equal(environment.buildingTilesetUrl, `/3dtiles/intersections/${id}/tileset.json`)
    assert.equal(environment.geojson.green, `/intersections/v3/${id}/green.geojson`)
    assert.equal(environment.geojson.water, `/intersections/v3/${id}/water.geojson`)
    assert.ok(Array.isArray(green.features))
    assert.ok(Array.isArray(water.features))
    assert.ok(green.features.every((feature) => feature.properties?.kind !== 'green-ground'))
    assert.equal(green.metadata.fallback, 'Baidu vector base map')
    assert.equal(water.metadata.fallback, 'Baidu vector base map')
    assert.ok(buildings.output_tile_count > 0, `${id} requires focused buildings`)
  }
})

test('intersection detail models require a browser-ready placement contract', () => {
  const environment = parseIntersectionEnvironmentManifest({
    schemaVersion: 1,
    intersectionId: 'demo_2',
    detailModel: {
      url: '/assets/intersection-pack/demo_2-detail.glb',
      position: [116.126756, 38.99115, 0],
      rotation: [Math.PI / 2, 0, 0],
      scale: 1,
    },
  }, 'demo_2')

  assert.equal(environment.detailModel.url, '/assets/intersection-pack/demo_2-detail.glb')
  assert.deepEqual(environment.detailModel.position, [116.126756, 38.99115, 0])
  assert.throws(() => parseIntersectionEnvironmentManifest({
    schemaVersion: 1,
    intersectionId: 'demo_2',
    detailModel: {
      url: '/assets/intersection-pack/raw.max',
      position: [116.126756, 38.99115],
      scale: 0,
    },
  }), /detail model is incomplete/)
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
    const firstCrosswalk = approach.crosswalkBars[0].center
    const along = (
      (firstCrosswalk[0] - approach.stopLineCenter[0]) * approach.tangent[0]
      + (firstCrosswalk[1] - approach.stopLineCenter[1]) * approach.tangent[1]
    ) / manifest.horizontalScale
    assert.ok(Math.abs(along - CROSSWALK_FIRST_CENTER_METERS) < 0.01)
    assert.ok(along > 0)
    assert.ok(approach.crosswalkBars.length >= 4)
    assert.ok(approach.crosswalkBars.every((bar) => (
      Math.abs(bar.length / manifest.horizontalScale - CROSSWALK_DEPTH_METERS) < 0.01
      && Math.abs(bar.width / manifest.horizontalScale - CROSSWALK_STRIPE_WIDTH_METERS) < 0.01
    )))
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

test('crosswalk bars span paired carriageways continuously without median gaps', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  for (const edge of manifest.edges.filter((candidate) => candidate.incoming)) {
    const approach = buildIntersectionApproachGeometry(edge, manifest.horizontalScale, manifest.edges)
    assert.ok(approach)
    assert.ok(approach.crosswalkHalfWidth >= approach.halfWidth)
    assert.ok(approach.crosswalkBars.length >= 4)
    assert.ok(approach.crosswalkBars.every((bar) => bar.length < approach.crosswalkHalfWidth * 2))
    const projections = approach.crosswalkCenters
      .map((point) => point[0] * approach.normal[0] + point[1] * approach.normal[1])
      .sort((left, right) => left - right)
    const maximumGap = Math.max(...projections.slice(1).map((value, index) => value - projections[index]))
    assert.ok(maximumGap / manifest.horizontalScale <= 0.91)
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

test('all compact intersections move crosswalks outward until their footprints no longer overlap', async () => {
  for (let index = 1; index <= 20; index += 1) {
    const id = `demo_${index}`
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/${id}/manifest.json`, import.meta.url),
      'utf8',
    ))
    const approaches = buildCollisionFreeIntersectionApproaches(manifest.edges, manifest.horizontalScale)
    for (let left = 0; left < approaches.length; left += 1) {
      for (let right = left + 1; right < approaches.length; right += 1) {
        assert.equal(
          crosswalksOverlap(
            approaches[left].geometry,
            approaches[right].geometry,
            manifest.horizontalScale,
          ),
          false,
          `${id} ${approaches[left].edge.id}/${approaches[right].edge.id}`,
        )
      }
    }
  }
})

test('crosswalk orientation follows the rebuilt road centerline instead of one skewed lane', () => {
  const edge = {
    id: 'incoming',
    incoming: true,
    centerline: [[-30, 0], [0, 0]],
    lanes: [
      { id: 'incoming_0', index: 0, kind: 'driving', width: 3.35, speed: 13.9, points: [[-30, -3.35], [-1, -3.35], [0, -3]] },
      { id: 'incoming_1', index: 1, kind: 'driving', width: 3.35, speed: 13.9, points: [[-30, 0], [0, 0]] },
      { id: 'incoming_2', index: 2, kind: 'driving', width: 3.35, speed: 13.9, points: [[-30, 3.35], [-1, 3.35], [0, 3]] },
    ],
  }
  const approach = buildIntersectionApproachGeometry(edge, 1, [edge])

  assert.ok(approach)
  assert.ok(Math.abs(approach.tangent[0] - 1) < 1e-9)
  assert.ok(Math.abs(approach.tangent[1]) < 1e-9)
  const first = approach.crosswalkCenters[0]
  const last = approach.crosswalkCenters.at(-1)
  assert.ok(Math.abs(first[0] - last[0]) < 1e-9)
  assert.ok(last[1] > first[1])
  assert.ok(approach.crosswalkBars.every((bar) => bar.length === CROSSWALK_DEPTH_METERS))
  assert.ok(approach.crosswalkBars.every((bar) => bar.width === CROSSWALK_STRIPE_WIDTH_METERS))
})

test('demo_2 lanes align with the authoritative WGS84 road export', async () => {
  const [manifestSource, roadsSource, catalogSource] = await Promise.all([
    readFile(new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url), 'utf8'),
    readFile(new URL('../public/showcase-data/demo_2.roads.wgs84.geojson', import.meta.url), 'utf8'),
    readFile(new URL('../public/intersections/v3/catalog.json', import.meta.url), 'utf8'),
  ])
  const manifest = JSON.parse(manifestSource)
  const roads = JSON.parse(roadsSource)
  const catalogEntry = JSON.parse(catalogSource).intersections.find((item) => item.intersectionId === 'demo_2')
  assert.ok(Math.abs(manifest.origin.longitude - catalogEntry.longitude) < 1e-10)
  assert.ok(Math.abs(manifest.origin.latitude - catalogEntry.latitude) < 1e-10)

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
  assert.ok(manifest.connections.every((item) => item.viaLaneId?.startsWith(':317_')))
  assert.ok(manifest.connections.every((item) => item.viaPoints?.length >= 2))
  assert.ok(manifest.connections.every((item) => item.renderPoints?.length >= 2))
  assert.ok(manifest.connections.every((item) => item.viaSegments?.length >= 1))
  assert.deepEqual(
    manifest.connections.find((item) => item.linkIndex === 3).viaSegments.map((item) => item.laneId),
    [':317_3_0', ':317_15_0'],
  )
  assert.deepEqual(
    manifest.connections.find((item) => item.linkIndex === 4).viaSegments.map((item) => item.laneId),
    [':317_3_1', ':317_15_1'],
  )
  assert.deepEqual(
    manifest.connections.find((item) => item.linkIndex === 12).viaSegments.map((item) => item.laneId),
    [':317_12_0', ':317_16_0'],
  )
})

test('demo_2 visual turn segments do not stretch SUMO speed into a jump', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  const length = (points) => points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
  for (const connection of manifest.connections) {
    for (const segment of connection.viaSegments) {
      const ratio = length(segment.renderPoints) / length(segment.points)
      assert.ok(
        ratio > 0.82 && ratio < 1.08,
        `${connection.linkIndex}:${segment.laneId} visual/source ratio is ${ratio.toFixed(3)}`,
      )
    }
  }
})
