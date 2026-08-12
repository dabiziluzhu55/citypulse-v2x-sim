import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { XMLParser } from 'fast-xml-parser'

import {
  normalizeRuntimeVehicleFlowId,
  resolveVehicleRouteTurnHint,
} from '../src/mapv/vehicleRouteTurnIndex.ts'
import {
  BUS_MODEL_PROFILE,
  CAR_MODEL_PROFILE,
  ELECTRIC_BICYCLE_MODEL_PROFILE,
  TRUCK_MODEL_PROFILE,
} from '../src/mapv/vehicleModelProfiles.ts'

const periods = ['morning_peak', 'off_peak', 'evening_peak']
const routeIndex = JSON.parse(await readFile(
  new URL('../src/assets/vehicle-route-turn-index.json', import.meta.url),
  'utf8',
))
const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: false,
})

function asArray(value) {
  if (value == null) return []
  return Array.isArray(value) ? value : [value]
}

function sha256(content) {
  return createHash('sha256').update(content).digest('hex')
}

function polylineLength(points) {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
}

function heading(points, end = false) {
  const left = end ? points.at(-2) : points[0]
  const right = end ? points.at(-1) : points[1]
  return Math.atan2(right[1] - left[1], right[0] - left[0])
}

function angleDeltaDegrees(left, right) {
  return Math.abs(Math.atan2(Math.sin(left - right), Math.cos(left - right))) * 180 / Math.PI
}

test('route turn index matches the current network and all three fixed route files', async () => {
  const network = await readFile(
    new URL('../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml', import.meta.url),
  )
  assert.equal(routeIndex.networkSource.sha256, sha256(network))
  assert.equal(Object.keys(routeIndex.connections).length, 421)
  for (const period of periods) {
    const content = await readFile(new URL(
      `../../data/maps/sumo/generated/traffic/global/${period}/routes.rou.xml`,
      import.meta.url,
    ))
    assert.equal(routeIndex.routeSources[period].sha256, sha256(content))
    assert.ok(Object.keys(routeIndex.periods[period].flows).length > 10_000)
  }
})

test('normalizes runtime ids and resolves every indexed lane/target-lane turn uniquely', () => {
  assert.equal(normalizeRuntimeVehicleFlowId('passenger_0_1229.0'), 'passenger_0_1229')
  let verified = 0
  for (const period of periods) {
    const periodIndex = routeIndex.periods[period]
    for (const [flowId, compactRouteIndex] of Object.entries(periodIndex.flows)) {
      for (const [routePosition, fromEdge, _toEdge, keys] of periodIndex.routes[compactRouteIndex]) {
        for (const key of keys) {
          const connection = routeIndex.connections[key]
          const hint = resolveVehicleRouteTurnHint({
            vehicle_id: `${flowId}.0`,
            longitude: null,
            latitude: null,
            x: 0,
            y: 0,
            speed: 1,
            angle: 0,
            road_id: fromEdge,
            lane_id: connection.fromLaneId,
            route_index: routePosition,
            target_lane_index: connection.toLaneIndex,
          }, period, connection.intersectionId)
          assert.equal(hint?.connectionKey, key, `${period}:${flowId}:${routePosition}:${key}`)
          verified += 1
        }
      }
    }
  }
  assert.ok(verified > 200_000)
})

test('keeps the locked connection unique through internal and shared outgoing lanes', () => {
  let verified = 0
  for (const period of periods) {
    const periodIndex = routeIndex.periods[period]
    for (const [flowId, compactRouteIndex] of Object.entries(periodIndex.flows)) {
      for (const [routePosition, _fromEdge, toEdge, keys] of periodIndex.routes[compactRouteIndex]) {
        for (const key of keys) {
          const connection = routeIndex.connections[key]
          const common = {
            vehicle_id: `${flowId}.0`,
            longitude: null,
            latitude: null,
            x: 0,
            y: 0,
            speed: 1,
            angle: 0,
          }
          const outgoing = resolveVehicleRouteTurnHint({
            ...common,
            road_id: toEdge,
            lane_id: connection.toLaneId,
            route_index: routePosition + 1,
          }, period, connection.intersectionId, key)
          assert.equal(outgoing?.connectionKey, key)
          for (const viaLaneId of new Set(connection.viaLaneIds)) {
            const internal = resolveVehicleRouteTurnHint({
              ...common,
              road_id: viaLaneId.split('_')[0],
              lane_id: viaLaneId,
              route_index: routePosition,
            }, period, connection.intersectionId, key)
            assert.equal(internal?.connectionKey, key)
          }
          verified += 1
        }
      }
    }
  }
  assert.ok(verified > 200_000)
})

test('vehicle guide lines preserve SUMO distance and attach every authoritative connection', async () => {
  let connectionCount = 0
  let guidedSegmentCount = 0
  let endpointLimitedCount = 0
  for (let index = 1; index <= 20; index += 1) {
    const manifest = JSON.parse(await readFile(new URL(
      `../public/intersections/v3/demo_${index}/manifest.json`,
      import.meta.url,
    )))
    assert.ok(Array.isArray(manifest.vehicleConnections))
    connectionCount += manifest.vehicleConnections.length
    const edgeById = new Map(manifest.edges.map((edge) => [edge.id, edge]))
    for (const edge of manifest.edges) {
      for (const lane of edge.lanes) {
        assert.equal(lane.vehicleGuidePoints.length, lane.vehicleGuideSourceStationsMeters.length)
        assert.ok(lane.vehicleGuideSourceStationsMeters.every((station, stationIndex, stations) => (
          stationIndex === 0 || station > stations[stationIndex - 1]
        )))
        const sourceLength = lane.vehicleGuideSourceStationsMeters.at(-1)
        const guideLength = polylineLength(lane.vehicleGuidePoints) / manifest.horizontalScale
        assert.ok(Math.abs(guideLength / sourceLength - 1) <= 0.02 + 1e-9)
      }
    }
    for (const connection of manifest.vehicleConnections) {
      const fromLane = edgeById.get(connection.fromEdge).lanes
        .find((lane) => lane.index === connection.fromLane)
      const toLane = edgeById.get(connection.toEdge).lanes
        .find((lane) => lane.index === connection.toLane)
      const guide = connection.vehicleGuidePoints
      assert.ok(guide?.length >= 2)
      assert.ok(Math.hypot(
        guide[0][0] - fromLane.vehicleGuidePoints.at(-1)[0],
        guide[0][1] - fromLane.vehicleGuidePoints.at(-1)[1],
      ) / manifest.horizontalScale <= 0.15)
      assert.ok(Math.hypot(
        guide.at(-1)[0] - toLane.vehicleGuidePoints[0][0],
        guide.at(-1)[1] - toLane.vehicleGuidePoints[0][1],
      ) / manifest.horizontalScale <= 0.15)
      assert.ok(angleDeltaDegrees(
        heading(guide),
        heading(fromLane.vehicleGuidePoints, true),
      ) <= 2)
      assert.ok(angleDeltaDegrees(
        heading(guide, true),
        heading(toLane.vehicleGuidePoints),
      ) <= 2)
      const sourceLength = connection.vehicleGuideSourceStationsMeters.at(-1)
      const guideLength = polylineLength(guide) / manifest.horizontalScale
      if (connection.vehicleGuideEndpointLimited) endpointLimitedCount += 1
      else assert.ok(Math.abs(guideLength / sourceLength - 1) <= 0.02 + 1e-9)
      for (const segment of connection.viaSegments ?? []) {
        const segmentGuide = segment.vehicleGuidePoints
        if (!segmentGuide) continue
        guidedSegmentCount += 1
        assert.equal(segmentGuide.length, segment.vehicleGuideSourceStationsMeters.length)
        assert.ok(segment.vehicleGuideSourceStationsMeters.every((station, stationIndex, stations) => (
          stationIndex === 0 || station > stations[stationIndex - 1]
        )))
      }
    }
  }
  assert.equal(connectionCount, 421)
  assert.ok(guidedSegmentCount >= 400)
  assert.equal(endpointLimitedCount, 0)
})

test('frontend physical vehicle lengths exactly match every SUMO route period', async () => {
  const expected = {
    official_passenger: CAR_MODEL_PROFILE.targetLengthMeters,
    official_bus: BUS_MODEL_PROFILE.targetLengthMeters,
    official_truck: TRUCK_MODEL_PROFILE.targetLengthMeters,
    official_electric_bicycle: ELECTRIC_BICYCLE_MODEL_PROFILE.targetLengthMeters,
  }
  assert.deepEqual(expected, {
    official_passenger: 5,
    official_bus: 12,
    official_truck: 10,
    official_electric_bicycle: 1.8,
  })
  for (const period of periods) {
    const document = parser.parse(await readFile(new URL(
      `../../data/maps/sumo/generated/traffic/global/${period}/routes.rou.xml`,
      import.meta.url,
    ), 'utf8'))
    for (const vehicleType of asArray(document.routes?.vType)) {
      if (!(vehicleType.id in expected)) continue
      assert.equal(Number(vehicleType.length), expected[vehicleType.id])
    }
  }
})
