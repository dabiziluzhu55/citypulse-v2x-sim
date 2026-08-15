import assert from 'node:assert/strict'
import test from 'node:test'

import { VehiclePresentationTimeline } from '../src/mapv/vehiclePresentationTimeline.ts'

function trafficView(elapsedSeconds, vehicles) {
  return {
    session_id: 'shared-clock',
    elapsed_seconds: elapsedSeconds,
    duration_seconds: 60,
    progress: elapsedSeconds / 60,
    official_time: '08:00:00',
    intersections: [],
    vehicles,
    metrics: null,
  }
}

function vehicle(id, longitude) {
  return {
    vehicle_id: id,
    longitude,
    latitude: 39,
    x: longitude,
    y: 0,
    speed: 4,
    angle: 90,
    road_id: 'edge',
    lane_id: 'edge_0',
  }
}

test('samples 2D and 3D from one delayed authoritative roster and time', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 8; sequence += 1) {
    timeline.push(
      trafficView(sequence * 0.5, [vehicle('shared', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 500,
    )
  }
  const elapsedSeconds = timeline.tick(4_000)
  const sample = timeline.sample()
  assert.equal(elapsedSeconds, 2)
  assert.equal(sample?.elapsedSeconds, 2)
  assert.equal(sample?.view.elapsed_seconds, sample?.elapsedSeconds)
  assert.deepEqual([...sample.authoritativeVehicleIds], ['shared'])
})

test('removes a vehicle exactly when the delayed authoritative roster removes it', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 9; sequence += 1) {
    timeline.push(
      trafficView(sequence * 0.5, sequence < 5 ? [vehicle('departed', 116)] : []),
      sequence,
      'RUNNING',
      1,
      sequence * 500,
    )
  }
  timeline.tick(4_500)
  const sample = timeline.sample()
  assert.equal(sample?.elapsedSeconds, 2.5)
  assert.deepEqual(sample?.view.vehicles, [])
  assert.deepEqual([...sample.authoritativeVehicleIds], [])
})

test('tracks an accelerated authoritative source without exceeding the shared delay window', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 12; sequence += 1) {
    timeline.push(
      trafficView(sequence, [vehicle('accelerated', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 100,
    )
    timeline.tick(sequence * 100)
  }
  const stats = timeline.stats()
  assert.ok(stats.observedSourceRate >= 9)
  assert.ok(stats.displayElapsedSeconds != null)
  assert.ok(12 - stats.displayElapsedSeconds <= 3)
})

test('reanchors to retained authority instead of reporting an expired display time', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 4; sequence += 1) {
    timeline.push(
      trafficView(sequence, [vehicle('retained', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 1_000,
    )
  }
  timeline.tick(4_000)
  for (let sequence = 5; sequence <= 20; sequence += 1) {
    timeline.push(
      trafficView(sequence, [vehicle('retained', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 1_000,
    )
  }
  const stats = timeline.stats()
  const sample = timeline.sample()
  assert.ok(stats.historyReanchorCount > 0)
  assert.ok((stats.displayElapsedSeconds ?? 0) >= 11)
  assert.equal(sample?.view.elapsed_seconds, stats.displayElapsedSeconds)
})
