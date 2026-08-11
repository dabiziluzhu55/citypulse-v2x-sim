import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { Matrix4, Vector3 } from 'three'

import {
  AdaptiveVehicleRenderBudget,
  BALANCED_VISIBLE_VEHICLES,
  CONSTRAINED_VISIBLE_VEHICLES,
  MAX_VISIBLE_VEHICLES,
  MAX_ROSTER_CHANGES_PER_SNAPSHOT,
  resolveVehicleRenderRadius,
  selectVisibleVehicles,
  StableVehicleSelector,
} from '../src/mapv/vehicleVisibility.ts'
import {
  createVehicleTwinSample,
  unwrapVehicleModelDirection,
} from '../src/mapv/vehicleTwinSample.ts'
import {
  isVehicleAnimationActive,
  VehiclePresentationClock,
} from '../src/mapv/vehiclePresentationClock.ts'
import {
  interpolateMonotonePathDistance,
  interpolateVehicleTwinSample,
  VehicleMotionBuffer,
} from '../src/mapv/vehicleMotionBuffer.ts'
import { VEHICLE_MODEL_BASE_Z } from '../src/mapv/sceneElevation.ts'
import {
  moveFromFrontBumperToModelCenter,
  resolveStableVehicleHeading,
  MAX_VEHICLE_HEADING_RATE,
  shortestAngleDelta,
  unwrapHeading,
} from '../src/mapv/vehicleOrientation.ts'
import {
  SumoHeadingField,
  sumoHeadingTransformIsValid,
  sumoNavigationAngleToMapHeading,
} from '../src/mapv/sumoHeadingTransform.ts'
import {
  createIntersectionLaneHeadingResolver,
  createIntersectionLanePoseResolver,
  mapSourceProgressToRenderDistance,
  visualStopFrontLimitDistance,
  VISUAL_STOP_BOUNDARY_CLEARANCE_METERS,
} from '../src/mapv/realistic/intersectionLaneHeading.ts'
import {
  samplePolyline,
  tangentAtProgress,
} from '../src/mapv/realistic/intersectionRoadGeometry.ts'
import {
  projectBd09ToWebMercator,
  projectSimulationCoordinateToBaiduMap,
  unprojectWebMercatorToBd09,
} from '../src/mapv/sceneCoordinates.ts'
import {
  BUS_MODEL_PROFILE,
  CAR_MODEL_PROFILE,
  ELECTRIC_BICYCLE_MODEL_PROFILE,
  TRUCK_MODEL_PROFILE,
  resolveVehicleModelProfile,
} from '../src/mapv/vehicleModelProfiles.ts'
import {
  MAX_VISUAL_BACKTRACK_METERS,
  classifyRoadTransition,
  minimumForwardTrackDistance,
  resolveCrossedStopLine,
  resolveVisualQueueConstraints,
  shouldAllowStopClamp,
  maximumStableVehicleDisplacementMeters,
  vehiclePoseDisplacementIsStable,
  reliableVehicleLanePosition,
  vehicleTelemetryIsPlaceholder,
} from '../src/mapv/vehiclePoseStability.ts'
import { snapshotToTrafficView } from '../src/utils/trafficStateMerge.ts'
import {
  MAX_VEHICLE_OUTPUT_CATCH_UP_RATE,
  VehicleOutputPacer,
} from '../src/mapv/vehicleOutputPacing.ts'

const vehicleRendererSource = await readFile(
  new URL('../src/mapv/BaiduVehicleRenderer.ts', import.meta.url),
  'utf8',
)
const headingManifest = JSON.parse(await readFile(
  new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
  'utf8',
))
const headingCatalog = JSON.parse(await readFile(
  new URL('../public/intersections/v3/catalog.json', import.meta.url),
  'utf8',
))
const TEST_SUMO_HEADING_TRANSFORM = headingManifest.sumoHeadingTransform
const mapHeading = (angleDegrees) => {
  const heading = sumoNavigationAngleToMapHeading(angleDegrees, TEST_SUMO_HEADING_TRANSFORM)
  assert.notEqual(heading, null)
  return heading
}

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
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.2,
      arrivalTimeMs: index * 200,
      samples: [motionSample('car', index * 2)],
    })
  }
  const first = buffer.sample(600)
  const second = buffer.sample(700)
  buffer.push({
    sceneGeneration: 0,
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
      sceneGeneration: 0,
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
        sceneGeneration: 0,
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
  buffer.push({ sceneGeneration: 0, sequence: 1, elapsedSeconds: 0, arrivalTimeMs: 0, samples: [motionSample('held', 0)] })
  buffer.push({ sceneGeneration: 0, sequence: 2, elapsedSeconds: 0.5, arrivalTimeMs: 500, samples: [motionSample('held', 1)] })
  buffer.push({ sceneGeneration: 0, sequence: 3, elapsedSeconds: 1, arrivalTimeMs: 1000, samples: [] })
  buffer.push({ sceneGeneration: 0, sequence: 4, elapsedSeconds: 1.4, arrivalTimeMs: 1400, samples: [] })
  const held = buffer.sample(1400)
  assert.ok(held?.some((sample) => sample.id === 'held'))
  buffer.push({ sceneGeneration: 0, sequence: 5, elapsedSeconds: 2, arrivalTimeMs: 2000, samples: [] })
  buffer.push({ sceneGeneration: 0, sequence: 6, elapsedSeconds: 2.5, arrivalTimeMs: 2500, samples: [] })
  let expired = null
  for (let wallTime = 1600; wallTime <= 5000; wallTime += 100) {
    expired = buffer.sample(wallTime)
  }
  assert.ok(expired && !expired.some((sample) => sample.id === 'held'))
})

test('keeps a continuously present roster visible across 1000 jittered source frames', () => {
  const buffer = new VehicleMotionBuffer()
  let renderedFrames = 0
  let arrivalTimeMs = 0
  for (let index = 0; index < 1_000; index += 1) {
    arrivalTimeMs += 42 + (index * 37) % 179
    const isolatedPoseFailure = index > 0 && index % 17 === 0
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.2,
      arrivalTimeMs,
      samples: isolatedPoseFailure ? [] : [motionSample('stable-car', index * 0.1)],
    })
    if (index < 5) continue
    const samples = buffer.sample(arrivalTimeMs + 24)
    if (!samples) continue
    renderedFrames += 1
    assert.ok(samples.some((sample) => sample.id === 'stable-car'), `vehicle flashed at frame ${index}`)
  }
  assert.ok(renderedFrames > 900)
})

test('deduplicates a vehicle id before buffering a source frame', () => {
  const buffer = new VehicleMotionBuffer()
  buffer.push({
    sceneGeneration: 0,
    sequence: 1,
    elapsedSeconds: 1,
    arrivalTimeMs: 1_000,
    samples: [motionSample('duplicate', 1), motionSample('duplicate', 2)],
  })
  buffer.push({
    sceneGeneration: 0,
    sequence: 2,
    elapsedSeconds: 2,
    arrivalTimeMs: 2_000,
    samples: [motionSample('duplicate', 3)],
  })
  buffer.push({
    sceneGeneration: 0,
    sequence: 3,
    elapsedSeconds: 3,
    arrivalTimeMs: 3_000,
    samples: [motionSample('duplicate', 4)],
  })
  buffer.push({
    sceneGeneration: 0,
    sequence: 4,
    elapsedSeconds: 4,
    arrivalTimeMs: 4_000,
    samples: [motionSample('duplicate', 5)],
  })
  const samples = buffer.sample(4_000)
  assert.equal(samples?.filter((sample) => sample.id === 'duplicate').length, 1)
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

test('preserves stabilized source headings while interpolating position', () => {
  const sample = interpolateVehicleTwinSample(
    motionSample('moving-east', 0, Math.PI / 2),
    motionSample('moving-east', 0.001, Math.PI / 2),
    0.5,
  )
  assert.ok(Math.abs(shortestAngleDelta(sample.vehicleHeading, Math.PI / 2)) < 1e-9)
  assert.ok(Math.abs(shortestAngleDelta(sample.dir, Math.PI / 2)) < 1e-9)
})

test('adapts motion buffering and keeps the presentation clock continuous on underrun', () => {
  const buffer = new VehicleMotionBuffer()
  const arrivals = [0, 500, 1_250, 1_750, 3_250]
  arrivals.forEach((arrivalTimeMs, index) => buffer.push({
    sceneGeneration: 0,
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
  assert.ok(stats.bufferSeconds >= 0.75 && stats.bufferSeconds <= 3.5)
  assert.ok(stats.sourceGapP95Ms >= 750)
  assert.ok(stats.sourceGapP99Ms >= stats.sourceGapP95Ms)
  assert.equal(stats.underrunActive, true)
  assert.ok(stats.underrunCount >= 1)
  assert.ok(stats.renderElapsedSeconds >= beforeUnderrun)
  const beforeNextOutput = stats.renderElapsedSeconds
  buffer.sample(8_000)
  assert.ok(buffer.stats().renderElapsedSeconds > beforeNextOutput)
  assert.ok(stats.targetBufferSeconds >= 1.5 && stats.targetBufferSeconds <= 3)
})

test('never mixes source frames from different scene generations', () => {
  const buffer = new VehicleMotionBuffer()
  assert.equal(buffer.push({
    sceneGeneration: 0,
    sequence: 1,
    elapsedSeconds: 1,
    arrivalTimeMs: 100,
    samples: [motionSample('old', 0)],
  }), true)
  assert.equal(buffer.push({
    sceneGeneration: 1,
    sequence: 2,
    elapsedSeconds: 2,
    arrivalTimeMs: 200,
    samples: [motionSample('new', 1)],
  }), true)
  assert.equal(buffer.stats().queuedFrames, 1)
  assert.equal(buffer.push({
    sceneGeneration: 0,
    sequence: 3,
    elapsedSeconds: 3,
    arrivalTimeMs: 300,
    samples: [motionSample('stale', 2)],
  }), false)
  assert.equal(buffer.stats().queuedFrames, 1)
})

test('keeps a vehicle at fixed scale while isolating incompatible motion epochs', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 2; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index,
      arrivalTimeMs: index * 1_000,
      samples: [{
        ...motionSample('respawned', index < 3 ? index : 30 + index - 3),
        sceneGeneration: 0,
        motionEpoch: index < 3 ? 0 : 1,
      }],
    })
  }
  buffer.sample(2_000)
  for (let index = 3; index <= 5; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index,
      arrivalTimeMs: index * 1_000,
      samples: [{
        ...motionSample('respawned', 30 + index - 3),
        sceneGeneration: 0,
        motionEpoch: 1,
      }],
    })
  }
  const outputs = []
  for (let wallTime = 5_000; wallTime <= 20_000; wallTime += 100) {
    const samples = buffer.sample(wallTime)
    outputs.push(...(samples ?? []))
  }
  assert.ok(outputs.some((sample) => sample.point[1] >= 30))
  assert.ok(outputs.every((sample) => sample.point[1] <= 2 || sample.point[1] >= 30))
  assert.ok(outputs.every((sample) => sample.scale.every((value) => value === 1)))
  assert.ok(outputs.every((sample) => !('transitionVisibility' in sample)))
})

test('classifies normal road changes without creating an incompatible transition', () => {
  const previous = { roadId: 'incoming', laneId: 'incoming_0', motionPathKey: 'route:turn' }
  assert.equal(classifyRoadTransition({
    previous,
    roadId: ':junction',
    laneId: ':junction_0_0',
    motionPathKey: 'route:turn',
    laneTransitionKind: 'topological',
    laneChanging: false,
    rawFallback: false,
    displacementStable: true,
  }), 'same_path')
  assert.equal(classifyRoadTransition({
    previous,
    roadId: 'outgoing',
    laneId: 'outgoing_0',
    motionPathKey: 'raw:outgoing:outgoing_0',
    laneTransitionKind: 'raw_fallback',
    laneChanging: false,
    rawFallback: true,
    displacementStable: true,
    headingDeltaRadians: 10 * Math.PI / 180,
  }), 'raw_continuous')
  assert.equal(classifyRoadTransition({
    previous,
    roadId: 'unrelated',
    laneId: 'unrelated_0',
    motionPathKey: 'raw:unrelated:unrelated_0',
    laneTransitionKind: 'raw_fallback',
    laneChanging: false,
    rawFallback: true,
    displacementStable: false,
    headingDeltaRadians: Math.PI,
  }), 'incompatible')
})

test('keeps the original model size while interpolating a road transition', () => {
  const left = {
    ...motionSample('constant-size', 0),
    scale: [1.2, 1.1, 0.9],
    motionPathKey: 'raw:edge-a:edge-a_0',
  }
  const right = {
    ...motionSample('constant-size', 0.001),
    scale: [0.1, 0.1, 0.1],
    motionPathKey: 'raw:edge-b:edge-b_0',
    roadTransitionKind: 'raw_continuous',
  }
  const halfway = interpolateVehicleTwinSample(left, right, 0.5)
  assert.deepEqual(halfway.scale, left.scale)
})

test('smoothly bridges a validated same-lane topology fallback', () => {
  const left = {
    ...motionSample('same-lane-fallback', 0),
    motionPathKey: 'route:turn:lane-0',
    vehicleHeading: 0,
  }
  const right = {
    ...motionSample('same-lane-fallback', 0.001),
    motionPathKey: 'raw:edge:lane-0',
    vehicleHeading: 0,
    roadTransitionKind: 'same_path',
    transitionKind: 'raw_fallback',
  }
  const halfway = interpolateVehicleTwinSample(left, right, 0.5)
  assert.ok(halfway.point[0] > left.point[0])
  assert.ok(halfway.point[0] < right.point[0])
})

test('uses 30 fps Twin keyframes and never batches gap-fill frames into one render', () => {
  assert.match(vehicleRendererSource, /NORMAL_TWIN_OUTPUT_FPS = 30/)
  assert.match(vehicleRendererSource, /STABLE_TWIN_OUTPUT_FPS = 24/)
  assert.equal(MAX_VEHICLE_OUTPUT_CATCH_UP_RATE, 1.1)
  assert.doesNotMatch(vehicleRendererSource, /for \(const outputTime of outputTimes\)/)
  assert.doesNotMatch(vehicleRendererSource, /motionEpoch \+=|motionEpoch\+\+/)
})

test('paces a 24 fps Twin stream correctly on a 60 hz browser clock', () => {
  const pacer = new VehicleOutputPacer()
  const outputs = []
  for (let frame = 0; frame <= 60; frame += 1) {
    const output = pacer.next(frame * 1_000 / 60, 1_000 / 24)
    if (output) outputs.push(output)
  }
  assert.ok(outputs.length >= 24 && outputs.length <= 25)
  assert.ok(outputs.every((output) => output.backlogMs < 1e-6))
})

test('recovers a long frame without advancing the vehicle clock above 110 percent', () => {
  const pacer = new VehicleOutputPacer()
  const interval = 1_000 / 30
  const first = pacer.next(0, interval)
  const second = pacer.next(interval, interval)
  const recovered = pacer.next(interval + 250, interval)
  assert.ok(first && second && recovered)
  assert.ok(recovered.catchingUp)
  assert.ok(
    recovered.sampleWallTimeMs - second.sampleWallTimeMs
      <= interval * MAX_VEHICLE_OUTPUT_CATCH_UP_RATE + 1e-9,
  )
})

test('does not turn held recovery poses into authoritative motion keyframes', () => {
  const buffer = new VehicleMotionBuffer()
  const frames = [
    { elapsedSeconds: 0, x: 0, sampleQuality: 'authoritative' },
    { elapsedSeconds: 0.5, x: 0, sampleQuality: 'held' },
    { elapsedSeconds: 1, x: 10, sampleQuality: 'authoritative' },
    { elapsedSeconds: 1.5, x: 15, sampleQuality: 'authoritative' },
    { elapsedSeconds: 2, x: 20, sampleQuality: 'authoritative' },
  ]
  frames.forEach((frame, sequence) => buffer.push({
    sceneGeneration: 0,
    sequence,
    elapsedSeconds: frame.elapsedSeconds,
    arrivalTimeMs: frame.elapsedSeconds * 1_000,
    samples: [{ ...motionSample('recovering', frame.x), sampleQuality: frame.sampleQuality }],
  }))
  const samples = buffer.sample(2_000)
  assert.ok(samples)
  assert.ok(samples[0].point[0] > 4.9, 'held pose incorrectly flattened the source curve')
})

test('predicts through jitter and reconciles restored topology data without a forward jump', () => {
  const metersPerLongitude = Math.cos(39 * Math.PI / 180) * 110_900
  const topologySample = (distanceMeters) => ({
    ...motionSample('smooth-car', 116 + distanceMeters / metersPerLongitude, 0),
    point: [116 + distanceMeters / metersPerLongitude, 39, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: 'straight',
    segmentKey: 'lane-0',
    pathArcDistanceMeters: distanceMeters,
    sourceSpeedMetersPerSecond: 10,
    transitionKind: 'same_lane',
    poseSource: 'topology',
    sampleQuality: 'authoritative',
    sceneGeneration: 0,
    motionEpoch: 0,
  })
  const buffer = new VehicleMotionBuffer()
  buffer.setMotionPathSampler({
    project(_key, coordinate) {
      return {
        pathArcDistanceMeters: (coordinate[0] - 116) * metersPerLongitude,
        distanceMeters: 0,
      }
    },
    sample(_key, distanceMeters) {
      const clamped = Math.max(0, Math.min(200, distanceMeters))
      return {
        longitude: 116 + clamped / metersPerLongitude,
        latitude: 39,
        heading: 0,
        pathArcDistanceMeters: clamped,
      }
    },
  })
  for (let index = 0; index <= 3; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [topologySample(index * 5)],
    })
  }
  let previousDistance = -1
  let maximumStep = 0
  for (let wallTimeMs = 1_500; wallTimeMs <= 3_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const distance = Number(samples[0].pathArcDistanceMeters)
    if (previousDistance >= 0) maximumStep = Math.max(maximumStep, distance - previousDistance)
    assert.ok(distance + 1e-6 >= previousDistance)
    previousDistance = distance
  }
  assert.ok(buffer.stats().predictedVehicleCount > 0)
  buffer.push({
    sceneGeneration: 0,
    sequence: 4,
    elapsedSeconds: 3,
    arrivalTimeMs: 3_500,
    samples: [topologySample(30)],
  })
  for (let wallTimeMs = 3_550; wallTimeMs <= 4_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const distance = Number(samples[0].pathArcDistanceMeters)
    maximumStep = Math.max(maximumStep, distance - previousDistance)
    assert.ok(distance + 1e-6 >= previousDistance)
    previousDistance = distance
  }
  assert.ok(maximumStep <= 0.56, `unexpected catch-up step: ${maximumStep.toFixed(3)}m`)
})

test('does not interpolate between unrelated motion paths in the same epoch', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index < 4; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index,
      arrivalTimeMs: index * 1_000,
      samples: [{
        ...motionSample('path-switch', 116 + index * 0.00001, 0),
        vehicleHeading: 0,
        sceneGeneration: 0,
        motionEpoch: 0,
        motionPathKey: index < 2 ? 'route:a' : 'route:b',
        transitionKind: 'raw_fallback',
      }],
    })
  }
  for (let wallTime = 3_000; wallTime <= 5_000; wallTime += 50) buffer.sample(wallTime)
  assert.ok(buffer.stats().incompatiblePathInterpolationCount > 0)
})

test('confirms recovery with an exact motion path and segment instead of a shared occupancy lane', () => {
  assert.match(vehicleRendererSource, /pending\.motionPathKey === lanePose\.motionPathKey/)
  assert.match(vehicleRendererSource, /pending\.segmentKey === lanePose\.segmentKey/)
  assert.doesNotMatch(vehicleRendererSource, /pending\.occupancyKey === lanePose\.occupancyKey/)
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

test('removes missing vehicles from the selector without a second retention lifecycle', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  selector.select([vehicle('held', 116.001, 39)], (coordinate) => [...coordinate], center, 500, 's:1')
  assert.equal(
    selector.select([], (coordinate) => [...coordinate], center, 500, 's:2').length,
    0,
  )
})

test('keeps a present vehicle during repeated viewport refreshes', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  const present = vehicle('held', 116.001, 39)
  selector.select([present], (coordinate) => [...coordinate], center, 500, 's:1')
  for (let index = 0; index < 10; index += 1) {
    assert.equal(selector.select([present], (coordinate) => [...coordinate], center, 500, 's:1').length, 1)
  }
})

test('limits roster churn to 32 vehicles when the performance tier changes', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  const vehicles = Array.from({ length: MAX_VISIBLE_VEHICLES }, (_, index) => (
    vehicle(String(index), 116 + index * 0.000001, 39)
  ))
  const full = selector.select(vehicles, (coordinate) => [...coordinate], center, 1_400, 's:1', MAX_VISIBLE_VEHICLES)
  const constrained = selector.select(vehicles, (coordinate) => [...coordinate], center, 1_400, 's:2', CONSTRAINED_VISIBLE_VEHICLES)
  assert.equal(full.length - constrained.length, MAX_ROSTER_CHANGES_PER_SNAPSHOT)
})

test('paces Twin output and counts empty grace by source snapshots', () => {
  assert.match(vehicleRendererSource, /TWIN_INTERPOLATION_DELAY_MS = 250/)
  assert.match(vehicleRendererSource, /TWIN_INITIAL_SAMPLE_SPACING_MS = 50/)
  assert.match(vehicleRendererSource, /SOURCE_MISSING_GRACE_SNAPSHOTS = 3/)
  assert.match(vehicleRendererSource, /NORMAL_OUTPUT_FRAME_MS = 1_000 \/ NORMAL_TWIN_OUTPUT_FPS/)
  assert.match(vehicleRendererSource, /CONSTRAINED_OUTPUT_FRAME_MS = 1_000 \/ STABLE_TWIN_OUTPUT_FPS/)
  assert.match(vehicleRendererSource, /this\.twinPlaybackBacklogMs = pacing\.backlogMs/)
  assert.match(vehicleRendererSource, /this\.pushMotionFrameAt\(pacing\.sampleWallTimeMs, wallTimeMs\)/)
  assert.match(vehicleRendererSource, /recordSourceRoster\(context\.sequence/)
  assert.doesNotMatch(vehicleRendererSource, /emptyRenderStreak \+= 1/)
  assert.match(vehicleRendererSource, /!isVehicleAnimationActive\(this\.lastContext\.state\)/)
})

test('freezes the final stable Twin roster for every terminal simulation state', () => {
  assert.match(vehicleRendererSource, /context\.state === 'STOPPED'/)
  assert.match(vehicleRendererSource, /context\.state === 'COMPLETED'/)
  assert.match(vehicleRendererSource, /context\.state === 'FAILED'/)
  const terminalEmptyBlock = vehicleRendererSource.slice(
    vehicleRendererSource.indexOf('vehicles.length === 0'),
    vehicleRendererSource.indexOf('const renderableVehicles'),
  )
  assert.match(terminalEmptyBlock, /freezeTerminalPose\(\)/)
  assert.doesNotMatch(terminalEmptyBlock, /resetRuntime\(|twin\.reset\(/)
  const freezeBlock = vehicleRendererSource.slice(
    vehicleRendererSource.indexOf('private freezeTerminalPose'),
    vehicleRendererSource.indexOf('private recordTwinPushGap'),
  )
  assert.match(freezeBlock, /!this\.terminalFreezeActive/)
  assert.match(freezeBlock, /this\.lastSourceSamples/)
  assert.match(freezeBlock, /pausableTwin\.pause/)
})

test('preserves complete optional vehicle telemetry through the traffic view', () => {
  const source = {
    ...vehicle('telemetry', 116, 39),
    acceleration: -1.2,
    lane_index: 2,
    lane_position: 34.5,
    allowed_speed: 13.9,
    route_id: 'route-a',
    route_index: 3,
    distance: 120.25,
    next_intersection_id: 'demo_8',
    target_speed: 8,
    target_lane_index: 1,
  }
  const view = snapshotToTrafficView({
    session_id: 'session', state: 'RUNNING', sequence: 1, elapsed_seconds: 1,
    duration_seconds: 60, progress: 1 / 60, official_time: '14:30:01', playback_speed: 1,
    intersections: {}, vehicles: [source], events: [], error: null,
    metrics: { active_vehicles: 1, departed_vehicles: 1, arrived_vehicles: 0, remaining_vehicles: 0, halting_vehicles: 0, total_waiting_time: 0, mean_speed: 10 },
  })
  assert.deepEqual(view.vehicles[0], source)
})

test('prevents visual regression while backend route distance advances', () => {
  const previous = {
    telemetryReliable: true, backendDistance: 100, routeId: 'route-a', routeIndex: 2, laneId: 'lane-1',
    trackKey: 'track-1', trackDistanceMeters: 40, crossedStopLine: false,
    laneResolutionFailures: 0, longitude: 116, latitude: 39, heading: 0,
    elapsedSeconds: 1, motionEpoch: 0, lastSeenSequence: 1,
  }
  const next = { ...vehicle('car', 116, 39), distance: 101, route_id: 'route-a', route_index: 2 }
  assert.equal(minimumForwardTrackDistance(previous, next), 40 - MAX_VISUAL_BACKTRACK_METERS)
  assert.equal(minimumForwardTrackDistance(previous, { ...next, route_index: 3 }), undefined)
})

test('keeps mixed vehicle queues one metre apart and hides an impossible tail', () => {
  const constraints = resolveVisualQueueConstraints([
    { id: 'car', occupancyKey: 'lane', lanePosition: 50, naturalCenterDistanceMeters: 50, lengthMeters: 5 },
    { id: 'bus', occupancyKey: 'lane', lanePosition: 48, naturalCenterDistanceMeters: 48, lengthMeters: 10 },
    { id: 'tail', occupancyKey: 'lane', lanePosition: 35, naturalCenterDistanceMeters: 35, lengthMeters: 9, previousCenterDistanceMeters: 35 },
  ])
  assert.equal(constraints.get('bus').maximumCenterDistanceMeters, 41.5)
  assert.equal(constraints.get('tail').hidden, true)
})

test('ignores the combined backend placeholder telemetry without rejecting real zero positions', () => {
  const placeholder = {
    ...vehicle('placeholder', 116, 39),
    lane_position: 0,
    distance: 0,
    route_id: '',
    route_index: -1,
  }
  assert.equal(vehicleTelemetryIsPlaceholder(placeholder), true)
  assert.equal(reliableVehicleLanePosition(placeholder), undefined)
  assert.equal(vehicleTelemetryIsPlaceholder({ ...placeholder, route_id: 'route-a' }), false)
  assert.equal(reliableVehicleLanePosition({ ...placeholder, route_id: 'route-a' }), 0)
})

test('groups different route tracks by their shared physical outgoing lane', () => {
  const constraints = resolveVisualQueueConstraints([
    { id: 'left-turn', occupancyKey: 'lane:out_0', lanePosition: 22, naturalCenterDistanceMeters: 22, lengthMeters: 5 },
    { id: 'straight', occupancyKey: 'lane:out_0', lanePosition: 20, naturalCenterDistanceMeters: 20, lengthMeters: 5 },
  ])
  assert.equal(constraints.get('straight').maximumCenterDistanceMeters, 16)
})

test('uses a speed-aware outlier gate for same-lane positions', () => {
  const previous = { longitude: 116, latitude: 39, elapsedSeconds: 10 }
  assert.equal(maximumStableVehicleDisplacementMeters(0, 1), 6)
  assert.equal(maximumStableVehicleDisplacementMeters(10, 1), 22)
  assert.equal(vehiclePoseDisplacementIsStable(
    previous,
    { longitude: 116.00001, latitude: 39 },
    0,
    11,
  ), true)
  assert.equal(vehiclePoseDisplacementIsStable(
    previous,
    { longitude: 116.001, latitude: 39 },
    0,
    11,
  ), false)
})

test('never pulls a vehicle back after it has crossed the stop boundary', () => {
  const first = { ...vehicle('car', 116, 39), speed: 0, distance: 10, route_id: 'route-a', route_index: 0 }
  assert.equal(resolveCrossedStopLine(null, first, 12, 10, false), true)
  const crossed = {
    telemetryReliable: true, backendDistance: 10, routeId: 'route-a', routeIndex: 0, laneId: first.lane_id,
    trackKey: 'lane', trackDistanceMeters: 10, crossedStopLine: true,
    laneResolutionFailures: 0, longitude: 116, latitude: 39, heading: 0,
    elapsedSeconds: 1, motionEpoch: 0, lastSeenSequence: 1,
  }
  assert.equal(shouldAllowStopClamp(crossed, first), false)
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

test('converts SUMO headings through the projection Jacobian and unwraps across north', () => {
  assert.equal(sumoHeadingTransformIsValid(TEST_SUMO_HEADING_TRANSFORM), true)
  assert.ok(Math.abs(shortestAngleDelta(
    mapHeading(90),
    Math.atan2(
      TEST_SUMO_HEADING_TRANSFORM.xAxis[1],
      TEST_SUMO_HEADING_TRANSFORM.xAxis[0],
    ),
  )) < 1e-9)
  assert.ok(Math.abs(
    Math.abs(shortestAngleDelta(mapHeading(0), mapHeading(180))) - Math.PI,
  ) < 1e-9)
  assert.ok(Math.abs(
    Math.abs(shortestAngleDelta(mapHeading(90), mapHeading(270))) - Math.PI,
  ) < 1e-9)
  const previous = 359 * Math.PI / 180
  const next = unwrapHeading(previous, 1 * Math.PI / 180)
  assert.ok(Math.abs(shortestAngleDelta(previous, next) - 2 * Math.PI / 180) < 1e-9)
})

test('loads one valid heading transform per intersection from a shared SUMO source', () => {
  assert.equal(headingCatalog.intersections.length, 20)
  assert.equal(new Set(headingCatalog.intersections.map((entry) => entry.intersectionId)).size, 20)
  for (const entry of headingCatalog.intersections) {
    assert.equal(sumoHeadingTransformIsValid(entry.sumoHeadingTransform), true)
    assert.equal(entry.sumoHeadingTransform.sourceSha256, headingCatalog.sourceSha256)
    assert.ok(entry.sumoHeadingTransform.determinant > 0)
  }
})

test('uses the selected local transform and three nearby anchors in the global overview', () => {
  const field = new SumoHeadingField((coordinate) => [coordinate[0], coordinate[1], coordinate[2]])
  field.setAnchors(headingCatalog.intersections)
  const localEntry = headingCatalog.intersections.find((entry) => entry.intersectionId === 'demo_2')
  field.setPreferredIntersection('demo_2')
  const local = field.resolve(321.6, [localEntry.longitude, localEntry.latitude])
  assert.deepEqual(local.anchorIds, ['demo_2'])
  assert.equal(local.local, true)
  assert.ok(Math.abs(shortestAngleDelta(local.heading, mapHeading(321.6))) < 1e-9)
  field.setPreferredIntersection(null)
  const global = field.resolve(321.6, [116.11, 39])
  assert.equal(global.local, false)
  assert.equal(global.anchorIds.length, 3)
})

test('eliminates the reproduced 126 degree road-heading error at demo_2', () => {
  const corrected = mapHeading(321.6)
  const laneHeading = createIntersectionLaneHeadingResolver(headingManifest)('-51425_0', 20)
  const legacy = (90 - 321.6) * Math.PI / 180
  assert.ok(Math.abs(shortestAngleDelta(corrected, laneHeading)) < 0.1 * Math.PI / 180)
  assert.ok(Math.abs(shortestAngleDelta(legacy, laneHeading)) > 126 * Math.PI / 180)
  assert.doesNotMatch(vehicleRendererSource, /sumoAngleToMapHeading/)
})

test('unwraps Twin model directions across north without changing the physical heading', () => {
  const previous = 359 * Math.PI / 180
  const next = unwrapVehicleModelDirection(previous, 1 * Math.PI / 180, 0)
  assert.ok(Math.abs(next - 361 * Math.PI / 180) < 1e-9)
  assert.ok(Math.abs(shortestAngleDelta(next, 1 * Math.PI / 180)) < 1e-9)
})

test('applies each vehicle model forward-axis calibration exactly once', () => {
  const profiles = [
    CAR_MODEL_PROFILE,
    BUS_MODEL_PROFILE,
    TRUCK_MODEL_PROFILE,
    ELECTRIC_BICYCLE_MODEL_PROFILE,
  ]
  for (const profile of profiles) {
    for (const heading of [0, Math.PI / 2, Math.PI, Math.PI * 1.5]) {
      const sample = createVehicleTwinSample(
        vehicle(`model-${profile.modelType}-${heading}`, 116, 39),
        116,
        39,
        0,
        profile,
        heading,
        true,
      )
      assert.ok(Math.abs(shortestAngleDelta(
        sample.dir + profile.modelForwardAxisAngle,
        heading,
      )) < 2 * Math.PI / 180)
    }
  }
})

test('keeps the actual MapV Z-up instance matrix aligned with the physical heading', () => {
  const rotateToZUp = new Matrix4().makeRotationX(Math.PI / 2)
  for (const profile of [
    CAR_MODEL_PROFILE,
    BUS_MODEL_PROFILE,
    TRUCK_MODEL_PROFILE,
    ELECTRIC_BICYCLE_MODEL_PROFILE,
  ]) {
    for (const heading of [0, Math.PI / 2, Math.PI, Math.PI * 1.5, Math.PI * 1.85]) {
      const instanceRotation = new Matrix4().makeRotationZ(
        heading - profile.modelForwardAxisAngle,
      )
      const finalMatrix = instanceRotation.multiply(rotateToZUp)
      const forward = new Vector3(
        Math.cos(profile.modelForwardAxisAngle),
        Math.sin(profile.modelForwardAxisAngle),
        0,
      ).applyMatrix4(finalMatrix)
      const finalHeading = Math.atan2(forward.y, forward.x)
      assert.ok(Math.abs(shortestAngleDelta(finalHeading, heading)) < 2 * Math.PI / 180)
    }
  }
})

test('keeps topology heading authoritative and lane-change distance uniform', () => {
  const left = {
    ...motionSample('turn', 116, 0),
    point: [116, 39, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: 'route:test',
    pathArcDistanceMeters: 0,
    sourceSpeedMetersPerSecond: 5,
  }
  const right = {
    ...left,
    point: [116.0001, 39.0001, 1.1],
    vehicleHeading: Math.PI / 2,
    pathArcDistanceMeters: 10,
    sourceSpeedMetersPerSecond: 5,
    transitionKind: 'topological',
    time: 1_000,
  }
  const sampler = {
    project: () => null,
    sample: (_key, distanceMeters) => ({
      longitude: 116 + distanceMeters / 100_000,
      latitude: 39,
      heading: Math.PI / 3,
      pathArcDistanceMeters: distanceMeters,
    }),
  }
  const topologyMiddle = interpolateVehicleTwinSample(left, right, 0.5, sampler)
  assert.ok(Math.abs(shortestAngleDelta(topologyMiddle.vehicleHeading, Math.PI / 3)) < 1e-9)
  assert.ok(Math.abs(topologyMiddle.pathArcDistanceMeters - 5) < 1e-9)
  const conflictingSampler = {
    ...sampler,
    sample: (_key, distanceMeters) => ({
      longitude: 116 + distanceMeters / 100_000,
      latitude: 39,
      heading: Math.PI,
      pathArcDistanceMeters: distanceMeters,
    }),
  }
  const conflictingMiddle = interpolateVehicleTwinSample(left, right, 0.5, conflictingSampler)
  assert.ok(Math.abs(shortestAngleDelta(conflictingMiddle.vehicleHeading, Math.PI / 4)) < 1e-9)

  const laneChangeRight = {
    ...right,
    motionPathKey: 'lane:target',
    transitionKind: 'lane_change',
  }
  const laneChangePoints = Array.from({ length: 11 }, (_, index) => (
    interpolateVehicleTwinSample(left, laneChangeRight, index / 10)
  ))
  const steps = laneChangePoints.slice(1).map((sample, index) => {
    const previousPoint = laneChangePoints[index].point
    const latitude = (previousPoint[1] + sample.point[1]) / 2 * Math.PI / 180
    return Math.hypot(
      (sample.point[0] - previousPoint[0]) * Math.cos(latitude) * 110_900,
      (sample.point[1] - previousPoint[1]) * 110_900,
    )
  })
  assert.ok(Math.max(...steps) / Math.min(...steps) < 1.25)
  assert.ok(Math.abs(shortestAngleDelta(
    laneChangePoints[5].vehicleHeading,
    Math.PI / 4,
  )) < 1e-9)
})

test('monotone path interpolation never overshoots or creates a speed spike', () => {
  const durationSeconds = 2
  const samples = Array.from({ length: 101 }, (_, index) => (
    interpolateMonotonePathDistance(10, 22, 5, 7, durationSeconds, index / 100)
  ))
  assert.equal(samples[0].distanceMeters, 10)
  assert.equal(samples.at(-1).distanceMeters, 22)
  assert.ok(samples.every((sample, index) => (
    sample.distanceMeters >= 10
    && sample.distanceMeters <= 22
    && (index === 0 || sample.distanceMeters >= samples[index - 1].distanceMeters)
    && sample.speedMetersPerSecond <= 7.7 + 1e-6
  )))
  const maximumSampledAcceleration = Math.max(...samples.slice(1).map((sample, index) => (
    Math.abs(sample.speedMetersPerSecond - samples[index].speedMetersPerSecond)
      / (durationSeconds / 100)
  )))
  assert.ok(maximumSampledAcceleration <= 3.1)
})

test('locks the last reliable heading while a vehicle is stopped', () => {
  const first = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 8,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const moving = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 8,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 1,
  }, first.state)
  const stopped = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(35),
    speedMetersPerSecond: 0,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 2,
  }, moving.state)

  assert.ok(Math.abs(shortestAngleDelta(moving.heading, stopped.heading)) < 1e-9)
  assert.equal(stopped.state.moving, false)
})

test('uses the SUMO heading when a vehicle first appears already stopped', () => {
  const laneHeading = Math.PI / 2
  const stopped = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 0,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 10,
    laneHeading,
  }, null)

  assert.ok(Math.abs(shortestAngleDelta(stopped.heading, mapHeading(90))) < 1e-9)
})

test('keeps movement hysteresis through low-speed snapshots', () => {
  const first = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 2,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const second = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
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
      assert.ok(outgoingPose.occupancyKey.startsWith('path:route:'))
      assert.ok(
        distance(previousPose, outgoingPose) < 0.15,
        `${connection.linkIndex}:${toLane.id} exit center is discontinuous`,
      )
    }
  }
})

test('keeps all 315 SUMO connections path-distinct and rejects conflicting visual tangents', async () => {
  let connectionCount = 0
  let ambiguousOutgoingLaneCount = 0
  let rejectedVisualTangentCount = 0
  for (let index = 1; index <= 20; index += 1) {
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/demo_${index}/manifest.json`, import.meta.url),
      'utf8',
    ))
    const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
    const toCoordinate = (point) => unprojectWebMercatorToBd09([
      manifest.origin.webMercator[0] + point[0],
      manifest.origin.webMercator[1] + point[1],
    ])
    const pathsByOutgoingLane = new Map()
    for (const connection of manifest.connections) {
      connectionCount += 1
      const fromEdge = manifest.edges.find((edge) => edge.id === connection.fromEdge)
      const toEdge = manifest.edges.find((edge) => edge.id === connection.toEdge)
      const fromLane = fromEdge?.lanes.find((lane) => lane.index === connection.fromLane)
      const toLane = toEdge?.lanes.find((lane) => lane.index === connection.toLane)
      assert.ok(fromLane)
      if (!toLane) continue
      const segments = connection.viaSegments?.length
        ? connection.viaSegments
        : connection.viaLaneId && connection.viaPoints?.length && connection.renderPoints?.length
          ? [{ laneId: connection.viaLaneId, points: connection.viaPoints, renderPoints: connection.renderPoints }]
          : []
      let previousLaneId = fromLane.id
      let previousPose = resolver(
        fromLane.id,
        toCoordinate(fromLane.points.at(-1)),
        CAR_MODEL_PROFILE.targetLengthMeters / 2,
      )
      assert.ok(previousPose)
      for (const segment of segments) {
        let segmentPose = resolver(
          segment.laneId,
          toCoordinate(segment.points[0]),
          CAR_MODEL_PROFILE.targetLengthMeters / 2,
          previousLaneId,
          previousPose.trackKey,
          {
            speedMetersPerSecond: 5,
            expectedHeading: tangentAtProgress(segment.points, 0),
          },
        )
        if (!segmentPose) {
          rejectedVisualTangentCount += 1
          segmentPose = resolver(
            segment.laneId,
            toCoordinate(segment.points[0]),
            CAR_MODEL_PROFILE.targetLengthMeters / 2,
            previousLaneId,
            previousPose.trackKey,
            { speedMetersPerSecond: 5 },
          )
        }
        assert.ok(segmentPose, `demo_${index}:${connection.linkIndex}:${segment.laneId}`)
        assert.equal(segmentPose.transitionKind, 'topological')
        previousLaneId = segment.laneId
        previousPose = segmentPose
      }
      const outgoingExpectedHeading = tangentAtProgress(toLane.points, 0)
      assert.notEqual(outgoingExpectedHeading, null)
      let outgoingPose = resolver(
        toLane.id,
        toCoordinate(toLane.points[0]),
        CAR_MODEL_PROFILE.targetLengthMeters / 2,
        previousLaneId,
        previousPose.trackKey,
        {
          speedMetersPerSecond: 5,
          expectedHeading: outgoingExpectedHeading,
        },
      )
      if (!outgoingPose) {
        rejectedVisualTangentCount += 1
        outgoingPose = resolver(
          toLane.id,
          toCoordinate(toLane.points[0]),
          CAR_MODEL_PROFILE.targetLengthMeters / 2,
          previousLaneId,
          previousPose.trackKey,
          { speedMetersPerSecond: 5 },
        )
      }
      assert.ok(outgoingPose, `demo_${index}:${connection.linkIndex}:${toLane.id}`)
      assert.ok(
        outgoingPose.pathArcDistanceMeters + 1e-6 >= previousPose.pathArcDistanceMeters,
        `demo_${index}:${connection.linkIndex} reset its global path arc distance`,
      )
      const sampledOutgoing = resolver.motionPathSampler.sample(
        outgoingPose.motionPathKey,
        outgoingPose.pathArcDistanceMeters,
      )
      assert.ok(sampledOutgoing)
      const sampledHeadingDelta = Math.abs(shortestAngleDelta(
        sampledOutgoing.heading,
        outgoingExpectedHeading,
      ))
      if (sampledHeadingDelta <= 35 * Math.PI / 180) {
        assert.ok(Math.abs(shortestAngleDelta(sampledOutgoing.heading, outgoingPose.heading)) < 1e-6)
      } else {
        assert.ok(sampledHeadingDelta > 35 * Math.PI / 180)
      }
      if (segments.length > 0) assert.equal(outgoingPose.motionPathKey, previousPose.motionPathKey)
      else assert.equal(outgoingPose.transitionKind, 'topological')
      assert.ok(outgoingPose.occupancyKey.startsWith('path:route:'))
      const paths = pathsByOutgoingLane.get(toLane.id) ?? new Set()
      paths.add(outgoingPose.motionPathKey)
      pathsByOutgoingLane.set(toLane.id, paths)
    }
    ambiguousOutgoingLaneCount += [...pathsByOutgoingLane.values()]
      .filter((paths) => paths.size > 1).length
  }
  assert.equal(connectionCount, 315)
  assert.equal(ambiguousOutgoingLaneCount, 115)
  assert.ok(rejectedVisualTangentCount > 0)
})

test('classifies only adjacent lanes on the same road as a lane change', () => {
  const origin = { longitude: 116, latitude: 39 }
  const originPlane = projectBd09ToWebMercator([origin.longitude, origin.latitude])
  const manifest = {
    origin: { ...origin, webMercator: originPlane },
    horizontalScale: 1,
    edges: [{
      id: 'edge',
      incoming: false,
      lanes: [
        { id: 'edge_0', index: 0, width: 3.5, speed: 12, points: [[0, 0], [20, 0]], renderPoints: [[0, 0], [20, 0]] },
        { id: 'edge_1', index: 1, width: 3.5, speed: 12, points: [[0, 3.5], [20, 3.5]], renderPoints: [[0, 3.5], [20, 3.5]] },
      ],
    }],
    connections: [],
  }
  const resolver = createIntersectionLanePoseResolver(manifest, (coordinate) => coordinate)
  const toCoordinate = (point) => unprojectWebMercatorToBd09([
    originPlane[0] + point[0],
    originPlane[1] + point[1],
  ])
  const first = resolver('edge_0', toCoordinate([10, 0]), 2.5)
  assert.ok(first)
  const changed = resolver('edge_1', toCoordinate([11, 3.5]), 2.5, 'edge_0', first.trackKey, {
    speedMetersPerSecond: 5,
    expectedHeading: 0,
  })
  assert.ok(changed)
  assert.equal(changed.transitionKind, 'lane_change')
  assert.notEqual(changed.motionPathKey, first.motionPathKey)
})

test('rejects a nearby lane whose tangent conflicts with the current SUMO heading', () => {
  const origin = { longitude: 116, latitude: 39 }
  const originPlane = projectBd09ToWebMercator([origin.longitude, origin.latitude])
  const manifest = {
    origin: { ...origin, webMercator: originPlane },
    horizontalScale: 1,
    edges: [{
      id: 'edge',
      incoming: false,
      lanes: [{ id: 'edge_0', index: 0, width: 3.5, speed: 12, points: [[0, 0], [0, 20]], renderPoints: [[0, 0], [0, 20]] }],
    }],
    connections: [],
  }
  const resolver = createIntersectionLanePoseResolver(manifest, (coordinate) => coordinate)
  const coordinate = unprojectWebMercatorToBd09([originPlane[0], originPlane[1] + 10])
  assert.equal(resolver('edge_0', coordinate, 2.5, undefined, undefined, {
    speedMetersPerSecond: 5,
    expectedHeading: 0,
  }), null)
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

test('rejects an unconfirmed lane tangent that conflicts with the SUMO heading', () => {
  const previous = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 8,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const turning = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 8,
    current: { longitude: 116.00001, latitude: 39 },
    timeSeconds: 1,
    laneHeading: Math.PI / 2,
  }, previous.state)

  assert.ok(Math.abs(shortestAngleDelta(turning.heading, mapHeading(90))) < 1e-9)
})

test('uses a topology-confirmed tangent without the generic raw heading rate limit', () => {
  const previous = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 8,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
  }, null)
  const turning = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(0),
    speedMetersPerSecond: 8,
    current: { longitude: 116.00001, latitude: 39.00001 },
    timeSeconds: 0.2,
    laneHeading: Math.PI / 2,
    topologyConfirmed: true,
  }, previous.state)
  assert.ok(Math.abs(shortestAngleDelta(turning.heading, Math.PI / 2)) < 1e-9)
})

test('limits heading changes to 120 degrees per simulation second', () => {
  const targetHeading = mapHeading(270)
  const previous = resolveStableVehicleHeading({
    sourceMapHeading: mapHeading(90),
    speedMetersPerSecond: 8,
    current: { longitude: 116, latitude: 39 },
    timeSeconds: 0,
    laneHeading: 0,
  }, null)
  const turning = resolveStableVehicleHeading({
    sourceMapHeading: targetHeading,
    speedMetersPerSecond: 8,
    current: {
      longitude: 116 + Math.cos(targetHeading) / (Math.cos(39 * Math.PI / 180) * 110_900),
      latitude: 39 + Math.sin(targetHeading) / 110_900,
    },
    timeSeconds: 0.5,
    laneHeading: Math.PI,
  }, previous.state)
  assert.ok(Math.abs(Math.abs(shortestAngleDelta(previous.heading, turning.heading)) - MAX_VEHICLE_HEADING_RATE * 0.5) < 1e-9)
})

test('moves the model center behind the front bumper in cardinal directions', () => {
  const point = { longitude: 116, latitude: 39 }
  const north = moveFromFrontBumperToModelCenter(point, Math.PI / 2, 2.5)
  const east = moveFromFrontBumperToModelCenter(point, 0, 2.5)
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
