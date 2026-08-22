import assert from 'node:assert/strict'
import test from 'node:test'

import { VehiclePresentationTimeline } from '../src/mapv/vehiclePresentationTimeline.ts'
import { VehiclePresentationCoordinator } from '../src/mapv/vehiclePresentationCoordinator.ts'
import {
  interpolateCanonicalVehiclePosition,
  registerCanonicalVehicleLaneGeometry,
} from '../src/mapv/canonicalVehicleMotion.ts'

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

test('samples a curved source lane by arc length instead of cutting across it', () => {
  registerCanonicalVehicleLaneGeometry([{
    laneId: 'curve_0', edgeId: 'curve', kind: 'driving', intersectionId: 'demo_1',
    coordinates: [[116, 39], [116.001, 39.001], [116.002, 39]],
  }, {
    laneId: 'edge_0', edgeId: 'edge', kind: 'driving', intersectionId: 'demo_1',
    coordinates: [[115.99, 39], [116.01, 39]],
  }])
  const left = { ...vehicle('curved', 116), latitude: 39, lane_id: 'curve_0' }
  const right = { ...vehicle('curved', 116.002), latitude: 39, lane_id: 'curve_0' }
  const middle = interpolateCanonicalVehiclePosition(left, right, 0.5)
  assert.equal(middle.source, 'lane_frenet')
  assert.ok(Math.abs(middle.longitude - 116.001) < 1e-6)
  assert.ok(Math.abs(middle.latitude - 39.001) < 1e-6)
})

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
  assert.equal(elapsedSeconds, 1)
  assert.equal(sample?.elapsedSeconds, 1)
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
  const boundarySample = timeline.sample()
  assert.equal(boundarySample?.elapsedSeconds, 1.5)
  assert.deepEqual(boundarySample?.view.vehicles.map((item) => item.vehicle_id), ['departed'])
  timeline.tick(5_500)
  const removedSample = timeline.sample()
  assert.ok((removedSample?.elapsedSeconds ?? 0) >= 2.5)
  assert.deepEqual(removedSample?.view.vehicles, [])
  assert.deepEqual([...(removedSample?.authoritativeVehicleIds ?? [])], [])
})

test('does not mistake a bursty source for permission to accelerate a 1x presentation', () => {
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
  assert.ok(stats.displayElapsedSeconds <= 2)
  assert.ok(stats.rateCorrection <= 0.01 + 1e-9)
})

test('expands the shared delay for 5x Twin lookahead without exceeding four seconds', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 8; sequence += 1) {
    timeline.push(
      trafficView(sequence * 2.5, [vehicle('five-x', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      5,
      sequence * 500,
    )
  }
  const expandedDelay = timeline.stats().delaySeconds
  assert.equal(expandedDelay, 3)

  timeline.push(trafficView(22.5, [vehicle('five-x', 116.00009)]), 9, 'RUNNING', 1, 5_000)
  const reducedDelay = timeline.stats().delaySeconds
  assert.equal(reducedDelay, expandedDelay)

  timeline.push(trafficView(25, [vehicle('five-x', 116.0001)]), 10, 'RUNNING', 5, 6_200)
  assert.equal(timeline.stats().delaySeconds, 3.7)
  timeline.push(trafficView(27.5, [vehicle('five-x', 116.00011)]), 11, 'RUNNING', 5, 8_200)
  assert.equal(timeline.stats().delaySeconds, 4)
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

test('advances a long main-thread frame at no more than 101 percent wall-clock speed', () => {
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
  assert.ok(after - before <= 0.25 * 1.01 + 1e-9)
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
  assert.equal(presented.elapsedSeconds, 487)
  assert.equal(history.displayElapsedSeconds, presented.elapsedSeconds)
  assert.ok(history.frames.length >= 13)
  assert.ok(history.leftFrame?.elapsedSeconds <= presented.elapsedSeconds)
  assert.ok(history.rightFrame?.elapsedSeconds > presented.elapsedSeconds)
  assert.ok(history.frames[0].elapsedSeconds <= presented.elapsedSeconds - 6)
  assert.equal(history.frames.at(-1).elapsedSeconds, 490)
})

test('consumes the authority buffer through real 1x snapshot bursts without stop-and-catch-up', () => {
  const timeline = new VehiclePresentationTimeline()
  const burstIntervalsMs = [500, 1_000, 131, 1_157, 212, 500, 500, 500, 500, 500, 500, 500]
  const arrivals = [0]
  for (const interval of burstIntervalsMs) arrivals.push(arrivals.at(-1) + interval)
  let nextSequence = 0
  let previousDisplay = null
  let previousWallTime = null
  let minimumAdvance = Number.POSITIVE_INFINITY
  let maximumRate = 0
  const endWallTime = arrivals.at(-1)
  for (let wallTime = 0; wallTime <= endWallTime + 1e-6; wallTime += 1000 / 60) {
    while (nextSequence < arrivals.length && arrivals[nextSequence] <= wallTime + 1e-6) {
      timeline.push(
        trafficView(nextSequence * 0.5, [vehicle('bursty', 116 + nextSequence * 0.00001)]),
        nextSequence,
        'RUNNING',
        1,
        arrivals[nextSequence],
      )
      nextSequence += 1
    }
    const display = timeline.tick(wallTime)
    if (display != null && previousDisplay != null && previousWallTime != null) {
      const advance = display - previousDisplay
      const wallDeltaSeconds = (wallTime - previousWallTime) / 1_000
      minimumAdvance = Math.min(minimumAdvance, advance)
      maximumRate = Math.max(maximumRate, advance / wallDeltaSeconds)
    }
    if (display != null) {
      previousDisplay = display
      previousWallTime = wallTime
    }
  }
  const stats = timeline.stats()
  assert.equal(stats.state, 'playing')
  assert.equal(stats.starvationCount, 0)
  assert.ok(minimumAdvance > 0)
  assert.ok(maximumRate <= 1.01 + 1e-6)
  assert.ok((stats.displayElapsedSeconds ?? 0) <= (arrivals.length - 1) * 0.5)
})

test('never advances beyond authority and resumes once one second is rebuffered', () => {
  const timeline = new VehiclePresentationTimeline()
  for (let sequence = 0; sequence <= 10; sequence += 1) {
    timeline.push(
      trafficView(sequence * 0.5, [vehicle('starved', 116 + sequence * 0.00001)]),
      sequence,
      'RUNNING',
      1,
      sequence * 500,
    )
  }
  timeline.tick(5_000)
  let starvationWallTime = 5_000
  for (let wallTime = 5_050; wallTime <= 12_000; wallTime += 50) {
    timeline.tick(wallTime)
    assert.ok((timeline.stats().displayElapsedSeconds ?? 0) <= 5)
    if (timeline.stats().state === 'starved') {
      starvationWallTime = wallTime
      break
    }
  }
  const starved = timeline.stats()
  assert.equal(starved.state, 'starved')
  assert.equal(starved.starvationCount, 1)
  const held = starved.displayElapsedSeconds
  assert.equal(held, 5)
  timeline.push(trafficView(5.5, [vehicle('starved', 116.00011)]), 11, 'RUNNING', 1, starvationWallTime + 50)
  timeline.tick(starvationWallTime + 50)
  assert.equal(timeline.stats().state, 'starved')
  timeline.push(trafficView(6, [vehicle('starved', 116.00012)]), 12, 'RUNNING', 1, starvationWallTime + 100)
  timeline.tick(starvationWallTime + 100)
  assert.equal(timeline.stats().state, 'playing')
  assert.equal(timeline.stats().starvationCount, 1)
  timeline.tick(starvationWallTime + 200)
  assert.ok((timeline.stats().displayElapsedSeconds ?? 0) > held)
})

for (const sourceRate of [0.84, 0.90, 0.94, 1]) {
  test(`keeps a continuous authority-bounded presentation for a sustained ${sourceRate}x source`, () => {
    const timeline = new VehiclePresentationTimeline()
    const frameIntervalMs = 1_000 / 30
    const sourceIntervalMs = 500 / sourceRate
    let sequence = 0
    let nextSourceWallTimeMs = 0
    let previousDisplay = null
    let previousDisplayWallTime = null
    let maximumStationaryMs = 0
    let stationaryMs = 0
    let maximumRate = 0
    for (let wallTimeMs = 0; wallTimeMs <= 600_000; wallTimeMs += frameIntervalMs) {
      while (nextSourceWallTimeMs <= wallTimeMs + 1e-6) {
        timeline.push(
          trafficView(sequence * 0.5, [vehicle('sustainable', 116 + sequence * 0.000001)]),
          sequence,
          'RUNNING',
          1,
          nextSourceWallTimeMs,
        )
        sequence += 1
        nextSourceWallTimeMs += sourceIntervalMs
      }
      const display = timeline.tick(wallTimeMs)
      if (display != null && previousDisplay != null && previousDisplayWallTime != null) {
        const wallDeltaSeconds = (wallTimeMs - previousDisplayWallTime) / 1_000
        const advance = display - previousDisplay
        if (advance <= 1e-9) stationaryMs += wallDeltaSeconds * 1_000
        else stationaryMs = 0
        maximumStationaryMs = Math.max(maximumStationaryMs, stationaryMs)
        maximumRate = Math.max(maximumRate, advance / wallDeltaSeconds)
      }
      if (display != null) {
        previousDisplay = display
        previousDisplayWallTime = wallTimeMs
      }
      const latestElapsedSeconds = (sequence - 1) * 0.5
      assert.ok(display == null || display <= latestElapsedSeconds + 1e-9)
    }
    const stats = timeline.stats()
    assert.equal(stats.starvationCount, 0)
    assert.ok(maximumStationaryMs < 100, `stationary for ${maximumStationaryMs}ms`)
    assert.ok(maximumRate <= 1.01 + 1e-6)
    assert.ok(Math.abs(stats.observedSourceRate - sourceRate) < 0.01)
  })
}
