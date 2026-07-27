import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_VISIBLE_VEHICLES,
  resolveVehicleRenderRadius,
  selectVisibleVehicles,
  StableVehicleSelector,
} from '../src/mapv/vehicleVisibility.ts'
import { createVehicleTwinSample } from '../src/mapv/vehicleTwinSample.ts'
import { VEHICLE_MODEL_BASE_Z } from '../src/mapv/sceneElevation.ts'
import {
  moveFromFrontBumperToModelCenter,
  resolveStableVehicleHeading,
  shortestAngleDelta,
  sumoAngleToMapHeading,
  unwrapHeading,
} from '../src/mapv/vehicleOrientation.ts'
import { createIntersectionLaneHeadingResolver } from '../src/mapv/realistic/intersectionLaneHeading.ts'
import {
  CAR_MODEL_PROFILE,
  resolveVehicleModelProfile,
} from '../src/mapv/vehicleModelProfiles.ts'

function vehicle(id, longitude, latitude) {
  return {
    vehicle_id: id,
    longitude,
    latitude,
    x: 0,
    y: 0,
    speed: 10,
    angle: 90,
    road_id: 'road-1',
    lane_id: 'lane-1',
    type_id: 'global_official_passenger',
  }
}

test('selects only vehicles inside the camera render radius', () => {
  const center = [116, 39]
  const selected = selectVisibleVehicles([
    vehicle('near', 116.001, 39),
    vehicle('far', 116.05, 39),
    vehicle('missing', null, null),
  ], (coordinate) => [...coordinate], center, 500)

  assert.deepEqual(selected.map((item) => item.vehicle.vehicle_id), ['near'])
  assert.ok(selected[0].distanceMeters > 80 && selected[0].distanceMeters < 100)
})

test('prioritizes nearest vehicles and caps Twin instance count', () => {
  const center = [116, 39]
  const vehicles = Array.from({ length: MAX_VISIBLE_VEHICLES + 20 }, (_, index) => (
    vehicle(String(index), 116 + index * 0.000001, 39)
  ))
  const selected = selectVisibleVehicles(
    vehicles,
    (coordinate) => [...coordinate],
    center,
    1_400,
  )

  assert.equal(selected.length, MAX_VISIBLE_VEHICLES)
  assert.equal(selected[0].vehicle.vehicle_id, '0')
  assert.equal(selected.at(-1).vehicle.vehicle_id, String(MAX_VISIBLE_VEHICLES - 1))
})

test('keeps the camera render radius within stable bounds', () => {
  assert.equal(resolveVehicleRenderRadius(100), 420)
  assert.equal(resolveVehicleRenderRadius(1_000), 1_200)
  assert.equal(resolveVehicleRenderRadius(10_000), 1_650)
})

test('retains selected vehicle ids across distance-order jitter', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  const vehicles = Array.from({ length: MAX_VISIBLE_VEHICLES + 10 }, (_, index) => (
    vehicle(String(index), 116 + index * 0.000001, 39)
  ))
  const first = selector.select(vehicles, (coordinate) => [...coordinate], center, 1_400, 's:1')
  const jittered = vehicles.map((item, index) => ({
    ...item,
    longitude: item.longitude - (index >= MAX_VISIBLE_VEHICLES ? 0.0002 : 0),
  }))
  const second = selector.select(jittered, (coordinate) => [...coordinate], center, 1_400, 's:2')

  assert.deepEqual(
    second.map((item) => item.vehicle.vehicle_id).sort(),
    first.map((item) => item.vehicle.vehicle_id).sort(),
  )
})

test('keeps missing vehicles for three snapshots before removal', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  selector.select([vehicle('held', 116.001, 39)], (coordinate) => [...coordinate], center, 500, 's:1')
  for (let sequence = 2; sequence <= 4; sequence += 1) {
    assert.equal(
      selector.select([], (coordinate) => [...coordinate], center, 500, `s:${sequence}`).length,
      1,
    )
  }
  assert.equal(selector.select([], (coordinate) => [...coordinate], center, 500, 's:5').length, 0)
})

test('converts SUMO headings to map headings and unwraps across north', () => {
  assert.ok(Math.abs(sumoAngleToMapHeading(0) - Math.PI / 2) < 1e-9)
  assert.ok(Math.abs(sumoAngleToMapHeading(90)) < 1e-9)
  assert.ok(Math.abs(sumoAngleToMapHeading(180) - Math.PI * 1.5) < 1e-9)
  assert.ok(Math.abs(sumoAngleToMapHeading(270) - Math.PI) < 1e-9)
  const previous = 359 * Math.PI / 180
  const next = unwrapHeading(previous, 1 * Math.PI / 180)
  assert.ok(Math.abs(shortestAngleDelta(previous, next) - 2 * Math.PI / 180) < 1e-9)
})

test('locks the last reliable heading while a vehicle is stopped', () => {
  const first = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 8,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const moving = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 8,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 1,
  }, first.state)
  const stopped = resolveStableVehicleHeading({
    sumoAngleDegrees: 35,
    speedMetersPerSecond: 0,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 2,
  }, moving.state)

  assert.ok(Math.abs(shortestAngleDelta(moving.heading, stopped.heading)) < 1e-9)
  assert.equal(stopped.state.moving, false)
})

test('uses the lane tangent when a vehicle first appears already stopped', () => {
  const laneHeading = Math.PI / 2
  const stopped = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 0,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 10,
    laneHeading,
  }, null)

  assert.ok(Math.abs(shortestAngleDelta(stopped.heading, laneHeading)) < 1e-9)
})

test('keeps movement hysteresis through low-speed snapshots', () => {
  const first = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 2,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const second = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 0.6,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 1,
  }, first.state)

  assert.equal(second.state.moving, true)
})

test('resolves a lane heading from the realistic intersection manifest', () => {
  const resolveLaneHeading = createIntersectionLaneHeadingResolver({
    edges: [{
      id: 'edge',
      incoming: true,
      lanes: [{ id: 'edge_0', index: 0, width: 3.5, speed: 10, points: [[0, 0], [0, 10], [10, 10]] }],
    }],
  })

  assert.ok(Math.abs(shortestAngleDelta(resolveLaneHeading('edge_0', 2), Math.PI / 2)) < 1e-9)
  assert.ok(Math.abs(shortestAngleDelta(resolveLaneHeading('edge_0', 16), 0)) < 1e-9)
  assert.equal(resolveLaneHeading('missing', 0), null)
})

test('moves the model center behind the front bumper in cardinal directions', () => {
  const point = { longitude: 116, latitude: 39 }
  const north = moveFromFrontBumperToModelCenter(point, sumoAngleToMapHeading(0), 2.5)
  const east = moveFromFrontBumperToModelCenter(point, sumoAngleToMapHeading(90), 2.5)
  assert.ok(north.latitude < point.latitude)
  assert.ok(Math.abs(north.longitude - point.longitude) < 1e-12)
  assert.ok(east.longitude < point.longitude)
  assert.ok(Math.abs(east.latitude - point.latitude) < 1e-12)
})

test('uses the simulation vehicle type instead of lane hashing', () => {
  assert.equal(resolveVehicleModelProfile('global_official_passenger').modelType, 3)
  assert.equal(resolveVehicleModelProfile('city_bus').modelType, 6)
  assert.equal(resolveVehicleModelProfile('delivery_truck').modelType, 10)
})

test('creates the point-based payload required by MapV Twin interpolation', () => {
  const sample = createVehicleTwinSample(
    vehicle('vehicle-1', 116.501, 39.801),
    116.501,
    39.801,
    1_234,
    CAR_MODEL_PROFILE,
    0,
  )

  assert.equal(sample.id, 'vehicle-1')
  assert.equal(sample.dir, 0)
  assert.equal(sample.time, 1_234)
  assert.equal(sample.modelType, 3)
  assert.deepEqual(sample.scale, CAR_MODEL_PROFILE.scale)
  assert.equal(sample.color, '#4b5663')
  assert.ok(sample.point[0] < 116.501)
  assert.equal(sample.point[1], 39.801)
  assert.equal(sample.point[2], VEHICLE_MODEL_BASE_Z)
  assert.ok(sample.point[2] > 1.04)
  assert.equal('lng' in sample, false)
  assert.equal('lat' in sample, false)
})
