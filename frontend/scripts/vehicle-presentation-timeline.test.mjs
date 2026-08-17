import assert from 'node:assert/strict'
import test from 'node:test'

import { VehiclePresentationTimeline } from '../src/mapv/vehiclePresentationTimeline.ts'
import { VehiclePresentationCoordinator } from '../src/mapv/vehiclePresentationCoordinator.ts'

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
    for (let frame = 0; frame < 6; frame += 1) {
      timeline.tick(sequence * 100 + frame * (100 / 6))
    }
  }
  const stats = timeline.stats()
  assert.ok(stats.observedSourceRate >= 9)
  assert.ok(stats.displayElapsedSeconds != null)
  assert.ok(12 - stats.displayElapsedSeconds <= 3)
})

test('retains authority around the display cursor instead of jumping it forward', () => {
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
  assert.equal(stats.historyReanchorCount, 0)
  assert.ok((stats.displayElapsedSeconds ?? 0) < 11)
  assert.equal(sample?.view.elapsed_seconds, stats.displayElapsedSeconds)
})

test('advances a long main-thread frame at no more than 105 percent wall-clock speed', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 8; sequence += 1) {
    timeline.push(
      trafficView(sequence * 0.5, [vehicle('smooth', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 500,
    )
  }
  const before = timeline.tick(4_000)
  timeline.push(
    trafficView(4.5, [vehicle('smooth', 116.00009)]),
    9,
    'RUNNING',
    1,
    4_250,
  )
  const after = timeline.tick(4_250)
  assert.ok(before != null && after != null)
  assert.ok(after - before >= 0.25 - 1e-9)
  assert.ok(after - before <= 0.25 * 1.05 + 1e-9)
})

test('freezes a paused or terminal presentation on the latest authoritative endpoint', () => {
  for (const state of ['PAUSED', 'COMPLETED']) {
    const timeline = new VehiclePresentationTimeline()
    timeline.push(trafficView(687, [vehicle('endpoint', 116)]), 1, 'RUNNING', 1, 0)
    timeline.push(trafficView(822.5, [vehicle('endpoint', 116.001)]), 2, 'RUNNING', 1, 500)
    timeline.tick(500)
    timeline.push(trafficView(900, [vehicle('endpoint', 116.002)]), 3, state, 1, 1_000)

    assert.equal(timeline.tick(1_000), 900)
    assert.equal(timeline.sample()?.elapsedSeconds, 900)
  }
})

for (const fps of [24, 30]) {
  test(`does not accumulate clock drift during sustained ${fps} fps rendering`, () => {
    const timeline = new VehiclePresentationTimeline()
    let sequence = 0
    let nextSourceWallTimeMs = 0
    const frameIntervalMs = 1_000 / fps
    for (let wallTimeMs = 0; wallTimeMs <= 20_000; wallTimeMs += frameIntervalMs) {
      while (nextSourceWallTimeMs <= wallTimeMs + 1e-6) {
        timeline.push(
          trafficView(sequence * 0.5, [vehicle('steady', 116 + sequence * 0.000001)]),
          sequence,
          'RUNNING',
          1,
          nextSourceWallTimeMs,
        )
        sequence += 1
        nextSourceWallTimeMs += 500
      }
      timeline.tick(wallTimeMs)
    }
    const stats = timeline.stats()
    const latestElapsedSeconds = (sequence - 1) * 0.5
    assert.ok(stats.displayElapsedSeconds != null)
    assert.ok(
      latestElapsedSeconds - stats.displayElapsedSeconds <= stats.delaySeconds + 0.6,
      `clock drifted by ${latestElapsedSeconds - stats.displayElapsedSeconds}s`,
    )
  })
}

test('hydrates a late-mounted 3D renderer from authoritative history around the display time', () => {
  const coordinator = new VehiclePresentationCoordinator()
  for (let sequence = 0; sequence <= 980; sequence += 1) {
    const elapsedSeconds = sequence * 0.5
    coordinator.push(
      trafficView(elapsedSeconds, [vehicle('late-mounted', 116 + sequence * 0.000001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 500,
    )
  }
  coordinator.tick(490_000)
  const presented = coordinator.sample()
  const history = coordinator.authoritativeHistoryWindow(presented?.elapsedSeconds ?? null)
  assert.ok(presented && history)
  assert.equal(presented.elapsedSeconds, 488)
  assert.equal(history.displayElapsedSeconds, presented.elapsedSeconds)
  assert.ok(history.frames.length >= 13)
  assert.ok(history.leftFrame?.elapsedSeconds <= presented.elapsedSeconds)
  assert.ok(history.rightFrame?.elapsedSeconds > presented.elapsedSeconds)
  assert.ok(history.frames[0].elapsedSeconds <= presented.elapsedSeconds - 6)
  assert.equal(history.frames.at(-1).elapsedSeconds, 490)
})
