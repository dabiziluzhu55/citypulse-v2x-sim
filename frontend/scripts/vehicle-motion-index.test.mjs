import assert from 'node:assert/strict'
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

function vehicle(id, lane, station) {
  const point = lane.sourcePoints[0]
  const geo = lane.coordinates[0]
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
  assert.equal(index.networkSource.sha256, '1f997d9fa7fea5e91fd9cf7821a5a72f67396732830a27ff724a67921d1c9a36')
  assert.equal(index.laneCount, 5277)
  assert.equal(index.connectionCount, 6012)
  assert.ok(index.lanes.every((lane) => lane.sourcePoints.length === lane.coordinates.length))
  assert.ok(index.connections.every((connection) => (
    connection.viaLaneIds.length <= 1
  )))
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
