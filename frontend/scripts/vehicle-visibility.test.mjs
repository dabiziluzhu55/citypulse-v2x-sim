import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_VISIBLE_VEHICLES,
  resolveVehicleRenderRadius,
  selectVisibleVehicles,
} from '../src/mapv/vehicleVisibility.ts'
import { createVehicleTwinSample } from '../src/mapv/vehicleTwinSample.ts'

function vehicle(id, longitude, latitude) {
  return {
    vehicle_id: id,
    longitude,
    latitude,
    x: 0,
    y: 0,
    speed: 10,
    angle: 90,
    lane_id: 'lane-1',
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

test('creates the point-based payload required by MapV Twin interpolation', () => {
  const sample = createVehicleTwinSample(
    vehicle('vehicle-1', 116.501, 39.801),
    116.501,
    39.801,
    1_234,
    3,
  )

  assert.deepEqual(sample, {
    id: 'vehicle-1',
    point: [116.501, 39.801, 0],
    dir: Math.PI / 2,
    time: 1_234,
    modelType: 3,
  })
  assert.equal('lng' in sample, false)
  assert.equal('lat' in sample, false)
})
