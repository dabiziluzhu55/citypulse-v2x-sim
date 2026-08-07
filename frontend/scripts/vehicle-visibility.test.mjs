import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  AdaptiveVehicleRenderBudget,
  BALANCED_VISIBLE_VEHICLES,
  CONSTRAINED_VISIBLE_VEHICLES,
  MAX_VISIBLE_VEHICLES,
  MISSING_SNAPSHOT_GRACE,
  resolveVehicleRenderRadius,
  selectVisibleVehicles,
  StableVehicleSelector,
} from '../src/mapv/vehicleVisibility.ts'
import { createVehicleTwinSample } from '../src/mapv/vehicleTwinSample.ts'
import {
  isVehicleAnimationActive,
  VehiclePresentationClock,
} from '../src/mapv/vehiclePresentationClock.ts'
import {
  interpolateVehicleTwinSample,
  VehicleMotionBuffer,
} from '../src/mapv/vehicleMotionBuffer.ts'
import { VEHICLE_MODEL_BASE_Z } from '../src/mapv/sceneElevation.ts'
import {
  moveFromFrontBumperToModelCenter,
  resolveStableVehicleHeading,
  shortestAngleDelta,
  sumoAngleToMapHeading,
  unwrapHeading,
} from '../src/mapv/vehicleOrientation.ts'
import {
  createIntersectionLaneHeadingResolver,
  createIntersectionLanePoseResolver,
  mapSourceProgressToRenderDistance,
  visualStopFrontLimitDistance,
  VISUAL_STOP_BOUNDARY_CLEARANCE_METERS,
} from '../src/mapv/realistic/intersectionLaneHeading.ts'
import { samplePolyline } from '../src/mapv/realistic/intersectionRoadGeometry.ts'
import {
  projectBd09ToWebMercator,
  projectSimulationCoordinateToBaiduMap,
  unprojectWebMercatorToBd09,
} from '../src/mapv/sceneCoordinates.ts'
import {
  CAR_MODEL_PROFILE,
  ELECTRIC_BICYCLE_MODEL_PROFILE,
  resolveVehicleModelProfile,
} from '../src/mapv/vehicleModelProfiles.ts'

test('keeps Twin presentation timestamps monotonic across repeated frames', () => {
  const clock = new VehiclePresentationClock()
  assert.equal(clock.next(1_000), 1_000)
  assert.equal(clock.next(1_000), 1_001)
  assert.equal(clock.next(900), 1_002)
  clock.reset()
  assert.equal(clock.next(500), 500)
})

test('runs the single motion output clock only while simulation time advances', () => {
  assert.equal(isVehicleAnimationActive('RUNNING'), true)
  assert.equal(isVehicleAnimationActive('STARTING'), true)
  assert.equal(isVehicleAnimationActive('STOPPING'), true)
  assert.equal(isVehicleAnimationActive('PAUSED'), false)
  assert.equal(isVehicleAnimationActive('COMPLETED'), false)
})

function motionSample(id, x, direction = 0) {
  return {
    id,
    point: [x, 39, 1.1],
    dir: direction,
    time: 0,
    modelType: 3,
    scale: [1, 1, 1],
    color: '#fff',
  }
}

test('resamples genuine source frames into continuous moving output', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 3; index += 1) {
    buffer.push({
      sequence: index,
      elapsedSeconds: index * 0.2,
      arrivalTimeMs: index * 200,
      samples: [motionSample('car', index * 2)],
    })
  }
  const first = buffer.sample(600)
  const second = buffer.sample(700)
  buffer.push({
    sequence: 4,
    elapsedSeconds: 0.8,
    arrivalTimeMs: 800,
    samples: [motionSample('car', 8)],
  })
  const third = buffer.sample(800)
  assert.ok(first && second && third)
  const firstStep = second[0].point[0] - first[0].point[0]
  const secondStep = third[0].point[0] - second[0].point[0]
  assert.ok(firstStep > 0.5)
  assert.ok(secondStep > 0.5)
  assert.ok(Math.abs(firstStep - secondStep) < 0.2)
})

test('continues output after consumed source frames are pruned', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 3; index += 1) {
    buffer.push({
      sequence: index,
      elapsedSeconds: index * 0.2,
      arrivalTimeMs: index * 200,
      samples: [motionSample('car', index * 2)],
    })
  }
  let previousX = Number.NEGATIVE_INFINITY
  for (let wallTimeMs = 600; wallTimeMs <= 2_000; wallTimeMs += 100) {
    if (wallTimeMs >= 800 && wallTimeMs % 200 === 0) {
      const sequence = wallTimeMs / 200
      buffer.push({
        sequence,
        elapsedSeconds: sequence * 0.2,
        arrivalTimeMs: wallTimeMs,
        samples: [motionSample('car', sequence * 2)],
      })
    }
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples, `missing resampled output at ${wallTimeMs}ms`)
    assert.ok(samples[0].point[0] >= previousX)
    previousX = samples[0].point[0]
  }
})

test('retains vehicle samples through short empty source gaps', () => {
  const buffer = new VehicleMotionBuffer()
  buffer.push({ sequence: 1, elapsedSeconds: 0, arrivalTimeMs: 0, samples: [motionSample('held', 0)] })
  buffer.push({ sequence: 2, elapsedSeconds: 0.5, arrivalTimeMs: 500, samples: [motionSample('held', 1)] })
  buffer.push({ sequence: 3, elapsedSeconds: 1, arrivalTimeMs: 1000, samples: [] })
  buffer.push({ sequence: 4, elapsedSeconds: 1.4, arrivalTimeMs: 1400, samples: [] })
  const held = buffer.sample(1400)
  assert.ok(held?.some((sample) => sample.id === 'held'))
  buffer.push({ sequence: 5, elapsedSeconds: 2, arrivalTimeMs: 2000, samples: [] })
  buffer.push({ sequence: 6, elapsedSeconds: 2.5, arrivalTimeMs: 2500, samples: [] })
  let expired = null
  for (let wallTime = 1600; wallTime <= 5000; wallTime += 100) {
    expired = buffer.sample(wallTime)
  }
  assert.ok(expired && !expired.some((sample) => sample.id === 'held'))
})

test('interpolates a stationary heading through the shortest turn instead of spinning backwards', () => {
  const degrees = (value) => value * Math.PI / 180
  const sample = interpolateVehicleTwinSample(
    motionSample('turning', 0, degrees(350)),
    motionSample('turning', 0, degrees(10)),
    0.5,
  )
  assert.ok(Math.abs(sample.dir - Math.PI * 2) < 1e-9)
})

test('aligns a moving model heading with its interpolated displacement', () => {
  const sample = interpolateVehicleTwinSample(
    motionSample('moving-east', 0, Math.PI / 2),
    motionSample('moving-east', 0.001, Math.PI / 2),
    0.5,
  )
  assert.ok(Math.abs(shortestAngleDelta(sample.vehicleHeading, 0)) < 1e-9)
  assert.ok(Math.abs(shortestAngleDelta(sample.dir, 0)) < 1e-9)
})

test('adapts motion buffering to irregular source intervals and freezes on underrun', () => {
  const buffer = new VehicleMotionBuffer()
  const arrivals = [0, 500, 1_250, 1_750, 3_250]
  arrivals.forEach((arrivalTimeMs, index) => buffer.push({
    sequence: index,
    elapsedSeconds: index * 0.5,
    arrivalTimeMs,
    samples: [motionSample('jittered', index)],
  }))
  const initial = buffer.sample(3_250)
  assert.ok(initial)
  const beforeUnderrun = buffer.stats().renderElapsedSeconds
  for (let wallTime = 3_500; wallTime <= 7_000; wallTime += 250) buffer.sample(wallTime)
  const stats = buffer.stats()
  assert.ok(stats.bufferSeconds >= 0.5 && stats.bufferSeconds <= 2)
  assert.ok(stats.sourceGapP95Ms >= 750)
  assert.ok(stats.sourceGapP99Ms >= stats.sourceGapP95Ms)
  assert.equal(stats.underrunActive, true)
  assert.ok(stats.underrunCount >= 1)
  assert.ok(stats.renderElapsedSeconds >= beforeUnderrun)
  const frozen = stats.renderElapsedSeconds
  buffer.sample(8_000)
  assert.equal(buffer.stats().renderElapsedSeconds, frozen)
})

test('uses FPS hysteresis before reducing the visible vehicle budget', () => {
  const budget = new AdaptiveVehicleRenderBudget()
  let state = budget.state()
  assert.equal(state.limit, MAX_VISIBLE_VEHICLES)
  for (let wallTime = 0; wallTime <= 3_600; wallTime += 50) state = budget.recordFrame(wallTime)
  assert.equal(state.quality, 'constrained')
  assert.equal(state.limit, CONSTRAINED_VISIBLE_VEHICLES)
  budget.reset()
  for (let wallTime = 0; wallTime <= 3_600; wallTime += 30) state = budget.recordFrame(wallTime)
  assert.equal(state.quality, 'balanced')
  assert.equal(state.limit, BALANCED_VISIBLE_VEHICLES)
})

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

test('keeps missing vehicles through short stream dropouts before removal', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  selector.select([vehicle('held', 116.001, 39)], (coordinate) => [...coordinate], center, 500, 's:1')
  for (let sequence = 2; sequence <= MISSING_SNAPSHOT_GRACE + 1; sequence += 1) {
    assert.equal(
      selector.select([], (coordinate) => [...coordinate], center, 500, `s:${sequence}`).length,
      1,
    )
  }
  assert.equal(
    selector.select([], (coordinate) => [...coordinate], center, 500, `s:${MISSING_SNAPSHOT_GRACE + 2}`).length,
    0,
  )
})

test('does not age a retained vehicle on repeated viewport refreshes', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  selector.select([vehicle('held', 116.001, 39)], (coordinate) => [...coordinate], center, 500, 's:1')
  for (let index = 0; index < MISSING_SNAPSHOT_GRACE * 2; index += 1) {
    assert.equal(selector.select([], (coordinate) => [...coordinate], center, 500, 's:1').length, 1)
  }
  assert.equal(selector.select([], (coordinate) => [...coordinate], center, 500, 's:2').length, 1)
})

test('uses exit-radius hysteresis to absorb camera-boundary jitter', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  const entry = selector.select([vehicle('edge', 116.00535, 39)], (coordinate) => [...coordinate], center, 500, 's:1')
  assert.equal(entry.length, 1)
  const retained = selector.select([vehicle('edge', 116.0072, 39)], (coordinate) => [...coordinate], center, 500, 's:2')
  assert.equal(retained.length, 1)
  const removed = selector.select([vehicle('edge', 116.0083, 39)], (coordinate) => [...coordinate], center, 500, 's:3')
  assert.equal(removed.length, 0)
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

test('snaps a SUMO vehicle position onto the rebuilt visual lane', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  const lane = manifest.edges.find((edge) => edge.id === '-51425').lanes[0]
  const sourcePoint = samplePolyline(lane.points, 0.5)
  const expected = samplePolyline(lane.renderPoints, 0.5)
  const coordinate = unprojectWebMercatorToBd09([
    manifest.origin.webMercator[0] + sourcePoint[0],
    manifest.origin.webMercator[1] + sourcePoint[1],
  ])
  const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
  const pose = resolver(lane.id, coordinate)
  assert.ok(pose)
  const projected = projectBd09ToWebMercator([pose.longitude, pose.latitude])
  const local = [
    projected[0] - manifest.origin.webMercator[0],
    projected[1] - manifest.origin.webMercator[1],
  ]
  assert.ok(Math.hypot(local[0] - expected[0], local[1] - expected[1]) < 0.2)
})

test('keeps an internal SUMO turn lane on its visual connection curve', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  const connection = manifest.connections.find((item) => item.direction === 'l' && item.viaPoints?.length >= 4)
  assert.ok(connection)
  const sourcePoint = samplePolyline(connection.viaPoints, 0.5)
  const coordinate = unprojectWebMercatorToBd09([
    manifest.origin.webMercator[0] + sourcePoint[0],
    manifest.origin.webMercator[1] + sourcePoint[1],
  ])
  const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
  const frontPose = resolver(connection.viaLaneId, coordinate)
  const centerPose = resolver(connection.viaLaneId, coordinate, CAR_MODEL_PROFILE.targetLengthMeters / 2)
  assert.ok(frontPose)
  assert.ok(centerPose)
  assert.equal(centerPose.modelCenterResolved, true)
  const front = projectBd09ToWebMercator([frontPose.longitude, frontPose.latitude])
  const center = projectBd09ToWebMercator([centerPose.longitude, centerPose.latitude])
  const offset = Math.hypot(front[0] - center[0], front[1] - center[1])
  assert.ok(offset > 2 && offset < 4.5, `unexpected curved center offset ${offset}`)
  assert.ok(Number.isFinite(centerPose.heading))
})

test('keeps model centers continuous across every realistic lane boundary', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
  const toCoordinate = (point) => unprojectWebMercatorToBd09([
    manifest.origin.webMercator[0] + point[0],
    manifest.origin.webMercator[1] + point[1],
  ])
  const distance = (left, right) => {
    const a = projectBd09ToWebMercator([left.longitude, left.latitude])
    const b = projectBd09ToWebMercator([right.longitude, right.latitude])
    return Math.hypot(a[0] - b[0], a[1] - b[1]) / manifest.horizontalScale
  }

  for (const offsetMeters of [2.5, 4.5, 5]) {
    for (const connection of manifest.connections) {
      const fromEdge = manifest.edges.find((edge) => edge.id === connection.fromEdge)
      const toEdge = manifest.edges.find((edge) => edge.id === connection.toEdge)
      const fromLane = fromEdge.lanes.find((lane) => lane.index === connection.fromLane)
      const toLane = toEdge.lanes.find((lane) => lane.index === connection.toLane)
      let previousLaneId = fromLane.id
      let previousPose = resolver(
        fromLane.id,
        toCoordinate(fromLane.points.at(-1)),
        offsetMeters,
      )
      assert.ok(previousPose)
      for (const segment of connection.viaSegments) {
        const nextPose = resolver(
          segment.laneId,
          toCoordinate(segment.points[0]),
          offsetMeters,
          previousLaneId,
          previousPose.trackKey,
        )
        assert.ok(nextPose)
        assert.ok(
          distance(previousPose, nextPose) < 0.15,
          `${connection.linkIndex}:${segment.laneId} entry center is discontinuous`,
        )
        previousLaneId = segment.laneId
        previousPose = resolver(
          segment.laneId,
          toCoordinate(segment.points.at(-1)),
          offsetMeters,
          previousLaneId,
          nextPose.trackKey,
        )
        assert.ok(previousPose)
      }
      const outgoingPose = resolver(
        toLane.id,
        toCoordinate(toLane.points[0]),
        offsetMeters,
        previousLaneId,
        previousPose.trackKey,
      )
      assert.ok(outgoingPose)
      assert.ok(
        distance(previousPose, outgoingPose) < 0.15,
        `${connection.linkIndex}:${toLane.id} exit center is discontinuous`,
      )
    }
  }
})

test('smoothly absorbs visual path length differences away from lane boundaries', () => {
  for (const [sourceLength, renderLength] of [[10, 8.4], [10, 9.3], [10, 10.8]]) {
    const epsilon = 1e-5
    const startSlope = (
      mapSourceProgressToRenderDistance(epsilon, sourceLength, renderLength)
      - mapSourceProgressToRenderDistance(0, sourceLength, renderLength)
    ) / (epsilon * sourceLength)
    const endSlope = (
      mapSourceProgressToRenderDistance(1, sourceLength, renderLength)
      - mapSourceProgressToRenderDistance(1 - epsilon, sourceLength, renderLength)
    ) / (epsilon * sourceLength)
    assert.ok(Math.abs(startSlope - 1) < 0.001)
    assert.ok(Math.abs(endSlope - 1) < 0.001)
    const distances = Array.from({ length: 101 }, (_, index) => (
      mapSourceProgressToRenderDistance(index / 100, sourceLength, renderLength)
    ))
    assert.ok(distances.every((distance, index) => index === 0 || distance >= distances[index - 1]))
  }
})

test('uses actual rendered movement ahead of a conflicting lane tangent', () => {
  const previous = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 8,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const turning = resolveStableVehicleHeading({
    sumoAngleDegrees: 90,
    speedMetersPerSecond: 8,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 1,
    laneHeading: Math.PI / 2,
  }, previous.state)

  assert.ok(Math.abs(shortestAngleDelta(turning.heading, 0)) < 1e-9)
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
  assert.equal(resolveVehicleModelProfile('official_electric_bicycle').modelType, ELECTRIC_BICYCLE_MODEL_PROFILE.modelType)
  assert.notEqual(ELECTRIC_BICYCLE_MODEL_PROFILE.modelType, CAR_MODEL_PROFILE.modelType)
})

test('keeps a stationary red-light vehicle behind the visual stop line', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  const incomingEdge = manifest.edges.find((edge) => (
    edge.incoming && edge.lanes.some((lane) => lane.kind !== 'pedestrian' && lane.kind !== 'bicycle')
  ))
  const lane = incomingEdge.lanes.find((item) => item.kind !== 'pedestrian' && item.kind !== 'bicycle')
  const sourcePoint = lane.points.at(-1)
  const coordinate = unprojectWebMercatorToBd09([
    manifest.origin.webMercator[0] + sourcePoint[0],
    manifest.origin.webMercator[1] + sourcePoint[1],
  ])
  const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
  const redPose = resolver(lane.id, coordinate, CAR_MODEL_PROFILE.targetLengthMeters / 2, undefined, undefined, {
    speedMetersPerSecond: 0,
    laneRuntime: { vehicle_count: 1, halting_count: 1, mean_speed: 0, waiting_time: 5, occupancy: 0.1, role: 'incoming', lane_has_green: false, signal_state: 'r' },
  })
  const greenPose = resolver(lane.id, coordinate, CAR_MODEL_PROFILE.targetLengthMeters / 2, undefined, undefined, {
    speedMetersPerSecond: 0,
    laneRuntime: { vehicle_count: 1, halting_count: 1, mean_speed: 0, waiting_time: 5, occupancy: 0.1, role: 'incoming', lane_has_green: true, signal_state: 'G' },
  })
  const movingPose = resolver(lane.id, coordinate, CAR_MODEL_PROFILE.targetLengthMeters / 2, undefined, undefined, {
    speedMetersPerSecond: 1,
    laneRuntime: { vehicle_count: 1, halting_count: 0, mean_speed: 1, waiting_time: 0, occupancy: 0.1, role: 'incoming', lane_has_green: false, signal_state: 'r' },
  })
  assert.ok(redPose?.stopClamped)
  assert.equal(greenPose?.stopClamped, false)
  assert.equal(movingPose?.stopClamped, false)
  assert.equal(visualStopFrontLimitDistance(10, 1), 10 - 0.21 - VISUAL_STOP_BOUNDARY_CLEARANCE_METERS)
})

test('clamps stationary red-light entry lanes across all 20 intersections', async () => {
  for (let index = 1; index <= 20; index += 1) {
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/demo_${index}/manifest.json`, import.meta.url),
      'utf8',
    ))
    const incomingLanes = manifest.edges
      .filter((edge) => edge.incoming)
      .flatMap((edge) => edge.lanes)
      .filter((lane) => lane.kind !== 'pedestrian' && lane.kind !== 'bicycle')
    assert.ok(incomingLanes.length > 0, `demo_${index} has no motorized entry lanes`)
    const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
    for (const lane of incomingLanes) {
      const sourcePoint = lane.points.at(-1)
      const coordinate = unprojectWebMercatorToBd09([
        manifest.origin.webMercator[0] + sourcePoint[0],
        manifest.origin.webMercator[1] + sourcePoint[1],
      ])
      const pose = resolver(lane.id, coordinate, CAR_MODEL_PROFILE.targetLengthMeters / 2, undefined, undefined, {
        speedMetersPerSecond: 0,
        laneRuntime: { vehicle_count: 1, halting_count: 1, mean_speed: 0, waiting_time: 2, occupancy: 0.1, role: 'incoming', lane_has_green: false, signal_state: 'r' },
      })
      assert.ok(pose?.stopClamped, `demo_${index}:${lane.id} was not kept behind its stop line`)
    }
  }
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

test('does not apply a second bumper offset to a lane-resolved model center', () => {
  const sample = createVehicleTwinSample(
    vehicle('vehicle-centered', 116.501, 39.801),
    116.501,
    39.801,
    1_234,
    CAR_MODEL_PROFILE,
    Math.PI / 3,
    true,
  )

  assert.equal(sample.point[0], 116.501)
  assert.equal(sample.point[1], 39.801)
})
