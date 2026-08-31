import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { parseVehicleMotionIndex } from '../src/mapv/vehicleMotionIndex.ts'
import {
  interpolateCanonicalVehiclePosition,
  registerCanonicalVehicleMotionIndex,
  canonicalVehicleMotionAudit,
} from '../src/mapv/canonicalVehicleMotion.ts'
import { interpolateVehicleTwinSample } from '../src/mapv/vehicleMotionBuffer.ts'
import { projectSimulationCoordinateToBaiduMap } from '../src/mapv/sceneCoordinates.ts'

const index = parseVehicleMotionIndex(JSON.parse(await readFile(
  new URL('../public/intersections/v3/vehicle-motion-index.json', import.meta.url),
  'utf8',
)))
const networkSource = await readFile(
  new URL('../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml', import.meta.url),
)
const networkSourceSha256 = createHash('sha256').update(networkSource).digest('hex')

function vehicle(id, lane, station) {
  const target = Math.max(0, Math.min(lane.lengthMeters, station))
  let traversed = 0
  let point = lane.sourcePoints[0]
  let geo = lane.coordinates[0]
  for (let index = 1; index < lane.sourcePoints.length; index += 1) {
    const start = lane.sourcePoints[index - 1]
    const end = lane.sourcePoints[index]
    const length = Math.hypot(end[0] - start[0], end[1] - start[1])
    if (traversed + length + 1e-9 < target) {
      traversed += length
      continue
    }
    const ratio = length > 1e-9
      ? Math.max(0, Math.min(1, (target - traversed) / length))
      : 0
    const geoStart = lane.coordinates[index - 1]
    const geoEnd = lane.coordinates[index]
    point = [
      start[0] + (end[0] - start[0]) * ratio,
      start[1] + (end[1] - start[1]) * ratio,
    ]
    geo = [
      geoStart[0] + (geoEnd[0] - geoStart[0]) * ratio,
      geoStart[1] + (geoEnd[1] - geoStart[1]) * ratio,
    ]
    break
  }
  return {
    vehicle_id: id,
    longitude: geo[0],
    latitude: geo[1],
    x: point[0],
    y: point[1],
    speed: 8,
    angle: 0,
    road_id: lane.edgeId,
    lane_id: lane.laneId,
    lane_index: lane.laneIndex,
    lane_position: station,
  }
}

test('vehicle motion index matches the current SUMO generation', () => {
  assert.equal(index.networkSource.sha256, networkSourceSha256)
  assert.equal(index.laneCount, 5277)
  assert.equal(index.connectionCount, 6012)
  assert.ok(index.lanes.every((lane) => lane.sourcePoints.length === lane.coordinates.length))
  assert.ok(index.connections.every((connection) => (
    connection.viaLaneIds.length <= 1
  )))
})

test('prefers the exact internal-lane connection over its enclosing connection', () => {
  registerCanonicalVehicleMotionIndex(index)
  const laneById = new Map(index.lanes.map((lane) => [lane.laneId, lane]))
  const exactConnection = index.connections.find((candidate) => (
    candidate.fromLaneId.startsWith(':')
    && index.connections.some((enclosing) => (
      enclosing.connectionId !== candidate.connectionId
      && enclosing.viaLaneIds.includes(candidate.fromLaneId)
      && enclosing.toLaneId === candidate.toLaneId
    ))
  ))
  assert.ok(exactConnection)
  const fromLane = laneById.get(exactConnection.fromLaneId)
  const toLane = laneById.get(exactConnection.toLaneId)
  assert.ok(fromLane)
  assert.ok(toLane)

  const middle = interpolateCanonicalVehiclePosition(
    vehicle('internal-successor', fromLane, Math.max(0, fromLane.lengthMeters - 1)),
    vehicle('internal-successor', toLane, 1),
    0.5,
  )
  assert.equal(middle.resolved, true)
  assert.equal(middle.source, 'sumo_connection')
})

test('same-lane canonical motion follows SUMO lane station and tangent', () => {
  registerCanonicalVehicleMotionIndex(index)
  const lane = index.lanes.find((entry) => !entry.internal && entry.lengthMeters > 80)
  assert.ok(lane)
  const left = vehicle('same-lane', lane, 10)
  const right = vehicle('same-lane', lane, 30)
  const middle = interpolateCanonicalVehiclePosition(left, right, 0.5)
  assert.equal(middle.resolved, true)
  assert.equal(middle.source, 'lane_frenet')
  assert.equal(middle.laneId, lane.laneId)
  assert.ok(Math.abs(middle.laneStation - 20) < 1e-6)
  assert.ok(Number.isFinite(middle.headingRadians))
})

test('uses current SUMO x/y when cached lane position is stale', () => {
  registerCanonicalVehicleMotionIndex(index)
  const lane = index.lanes.find((entry) => !entry.internal && entry.lengthMeters > 80)
  assert.ok(lane)
  const current = vehicle('stale-station', lane, 30)
  current.lane_position = 10
  const endpoint = interpolateCanonicalVehiclePosition(current, current, 1)
  assert.equal(endpoint.resolved, true)
  assert.ok(Math.abs(endpoint.laneStation - 30) < 0.1)
})

test('unique SUMO connections replace geographic chord interpolation', () => {
  registerCanonicalVehicleMotionIndex(index)
  const connection = index.connections.find((entry) => entry.viaLaneIds.length === 1)
  assert.ok(connection)
  const fromLane = index.lanes.find((lane) => lane.laneId === connection.fromLaneId)
  const toLane = index.lanes.find((lane) => lane.laneId === connection.toLaneId)
  assert.ok(fromLane)
  assert.ok(toLane)
  const left = vehicle('turning', fromLane, Math.max(0, fromLane.lengthMeters - 5))
  const right = vehicle('turning', toLane, 5)
  const middle = interpolateCanonicalVehiclePosition(left, right, 0.5)
  assert.equal(middle.resolved, true)
  assert.equal(middle.source, 'sumo_connection')
  assert.equal(middle.routeEvidence, 'unique_connection')
  assert.ok(connection.viaLaneIds.includes(middle.laneId) || [connection.fromLaneId, connection.toLaneId].includes(middle.laneId))
})

test('resolves network-scale connection pairs without scanning all connections per vehicle', () => {
  registerCanonicalVehicleMotionIndex(index)
  const laneById = new Map(index.lanes.map((lane) => [lane.laneId, lane]))
  const candidates = index.connections.slice(0, 2_000)
  const startedAt = performance.now()
  for (const [connectionIndex, connection] of candidates.entries()) {
    const fromLane = laneById.get(connection.fromLaneId)
    const toLane = laneById.get(connection.toLaneId)
    assert.ok(fromLane)
    assert.ok(toLane)
    interpolateCanonicalVehiclePosition(
      vehicle(`indexed-${connectionIndex}`, fromLane, Math.max(0, fromLane.lengthMeters - 2)),
      vehicle(`indexed-${connectionIndex}`, toLane, 2),
      0.5,
    )
  }
  assert.ok(performance.now() - startedAt < 1_000)
})

test('an unproven cross-lane transition is hidden instead of drawn as a chord', () => {
  registerCanonicalVehicleMotionIndex(index)
  const leftLane = index.lanes.find((lane) => !lane.internal)
  const connected = new Set(index.connections
    .filter((connection) => connection.fromLaneId === leftLane.laneId)
    .map((connection) => connection.toLaneId))
  const rightLane = index.lanes.find((lane) => (
    !lane.internal
    && lane.edgeId !== leftLane.edgeId
    && !connected.has(lane.laneId)
  ))
  assert.ok(leftLane)
  assert.ok(rightLane)
  const result = interpolateCanonicalVehiclePosition(
    vehicle('unresolved', leftLane, 1),
    vehicle('unresolved', rightLane, 1),
    0.5,
  )
  assert.equal(result.resolved, false)
  assert.equal(result.source, 'unresolved')
  assert.equal(result.longitude, null)
  assert.ok(canonicalVehicleMotionAudit().unresolvedSegmentCount >= 1)
})

test('3D global interpolation samples the same canonical curve instead of its endpoint chord', () => {
  const curvedIndex = {
    schemaVersion: 1,
    networkSource: { path: 'test.net.xml', sha256: 'a'.repeat(64) },
    intersectionCatalogSha256: 'b'.repeat(64),
    coordinateSystems: { source: 'SUMO_XY_METERS', geographic: 'WGS84' },
    laneCount: 1,
    connectionCount: 0,
    lanes: [{
      laneId: 'curve_0', edgeId: 'curve', laneIndex: 0, intersectionId: 'demo_1',
      internal: false, widthMeters: 3.5, lengthMeters: 20,
      sourcePoints: [[0, 0], [10, 10], [20, 0]],
      coordinates: [[116, 39], [116.0001, 39.0001], [116.0002, 39]],
    }],
    connections: [],
  }
  registerCanonicalVehicleMotionIndex(curvedIndex)
  const leftVehicle = { ...vehicle('curve-3d', curvedIndex.lanes[0], 0), x: 0, y: 0 }
  const rightVehicle = { ...vehicle('curve-3d', curvedIndex.lanes[0], 28.284), x: 20, y: 0 }
  const canonical = interpolateCanonicalVehiclePosition(leftVehicle, rightVehicle, 0.5)
  assert.equal(canonical.resolved, true)
  const leftPoint = projectSimulationCoordinateToBaiduMap([116, 39, 1.1])
  const rightPoint = projectSimulationCoordinateToBaiduMap([116.0002, 39, 1.1])
  const middle = interpolateVehicleTwinSample({
    id: 'curve-3d', point: leftPoint, dir: 0, time: 0, modelType: 3,
    scale: [1, 1, 1], color: '#fff', vehicleHeading: 0, modelForwardAxisAngle: 0,
    motionPathKey: 'raw:curve:curve_0', canonicalSegmentId: 'previous',
    canonicalRouteEvidence: 'authoritative_endpoint', vehicleLengthMeters: 0,
  }, {
    id: 'curve-3d', point: rightPoint, dir: 0, time: 500, modelType: 3,
    scale: [1, 1, 1], color: '#fff', vehicleHeading: 0, modelForwardAxisAngle: 0,
    motionPathKey: 'raw:curve:curve_0', canonicalSegmentId: canonical.segmentId,
    canonicalRouteEvidence: canonical.routeEvidence, vehicleLengthMeters: 0,
  }, 0.5)
  const expected = projectSimulationCoordinateToBaiduMap([116.0001, 39.0001, 1.1])
  assert.ok(Math.abs(middle.point[0] - expected[0]) < 1e-6)
  assert.ok(Math.abs(middle.point[1] - expected[1]) < 1e-6)
})

test('keeps canonical endpoints on the sampled lane while retaining authoritative source XY', () => {
  const endpointIndex = {
    schemaVersion: 1,
    networkSource: { path: 'endpoint.net.xml', sha256: 'c'.repeat(64) },
    intersectionCatalogSha256: 'd'.repeat(64),
    coordinateSystems: { source: 'SUMO_XY_METERS', geographic: 'WGS84' },
    laneCount: 1,
    connectionCount: 0,
    lanes: [{
      laneId: 'endpoint_0', edgeId: 'endpoint', laneIndex: 0, intersectionId: 'demo_1',
      internal: false, widthMeters: 3.5, lengthMeters: 20,
      sourcePoints: [[0, 0], [20, 0]],
      coordinates: [[116, 39], [116.0002, 39]],
    }],
    connections: [],
  }
  registerCanonicalVehicleMotionIndex(endpointIndex)
  const offLane = {
    ...vehicle('endpoint', endpointIndex.lanes[0], 10),
    x: 10,
    y: 7,
    longitude: 116.0001,
    latitude: 39.00007,
  }
  const endpoint = interpolateCanonicalVehiclePosition(offLane, offLane, 1)
  assert.equal(endpoint.resolved, true)
  assert.equal(endpoint.sourceY, 7)
  assert.equal(endpoint.latitude, 39)
})

test('rejects an unconfirmed two-lane jump', () => {
  const lanes = [0, 1, 2].map((laneIndex) => ({
    laneId: `wide_${laneIndex}`, edgeId: 'wide', laneIndex, intersectionId: 'demo_1',
    internal: false, widthMeters: 3.5, lengthMeters: 40,
    sourcePoints: [[0, laneIndex * 3.5], [40, laneIndex * 3.5]],
    coordinates: [[116, 39 + laneIndex * 0.00003165], [116.0004, 39 + laneIndex * 0.00003165]],
  }))
  registerCanonicalVehicleMotionIndex({
    schemaVersion: 1,
    networkSource: { path: 'wide.net.xml', sha256: 'e'.repeat(64) },
    intersectionCatalogSha256: 'f'.repeat(64),
    coordinateSystems: { source: 'SUMO_XY_METERS', geographic: 'WGS84' },
    laneCount: lanes.length,
    connectionCount: 0,
    lanes,
    connections: [],
  })
  const left = { ...vehicle('wide-change', lanes[0], 10), x: 10, y: 0 }
  const right = { ...vehicle('wide-change', lanes[2], 15), x: 15, y: 7 }
  const unconfirmed = interpolateCanonicalVehiclePosition(
    left,
    right,
    0.5,
  )
  assert.equal(unconfirmed.resolved, false)
})
