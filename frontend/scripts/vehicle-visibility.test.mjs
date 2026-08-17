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
  compileMotionSegment,
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
  nearestPolylineProgress,
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
  classifyRoadTransition,
  resolveCrossedStopLine,
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
import {
  MapvTwinWindowProbe,
  VEHICLE_TWIN_PRIME_SPACING_MS,
  VEHICLE_TWIN_RENDER_DELAY_MS,
} from '../src/mapv/vehicleTwinPresentation.ts'

const vehicleRendererSource = await readFile(
  new URL('../src/mapv/BaiduVehicleRenderer.ts', import.meta.url),
  'utf8',
)
const vehicleTwinPresenterSource = await readFile(
  new URL('../src/mapv/vehicleTwinPresenter.ts', import.meta.url),
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
  for (let index = 0; index <= 12; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.2,
      arrivalTimeMs: index * 200,
      samples: [motionSample('car', index * 2)],
    })
  }
  const first = buffer.sample(2_400)
  const second = buffer.sample(2_500)
  buffer.push({
    sceneGeneration: 0,
    sequence: 13,
    elapsedSeconds: 2.6,
    arrivalTimeMs: 2_600,
    samples: [motionSample('car', 26)],
  })
  const third = buffer.sample(2_600)
  assert.ok(first && second && third)
  const firstStep = second[0].point[0] - first[0].point[0]
  const secondStep = third[0].point[0] - second[0].point[0]
  assert.ok(firstStep > 0.9)
  assert.ok(secondStep > 0.9)
  assert.ok(Math.abs(firstStep - secondStep) < 0.1)
})

test('compiles adjacent received snapshots even when backend sequence numbers skip', () => {
  const buffer = new VehicleMotionBuffer()
  ;[
    { sequence: 10, elapsedSeconds: 0, x: 0 },
    { sequence: 12, elapsedSeconds: 0.5, x: 5 },
    { sequence: 15, elapsedSeconds: 1, x: 10 },
  ].forEach((frame, index) => buffer.push({
    sceneGeneration: 0,
    sequence: frame.sequence,
    elapsedSeconds: frame.elapsedSeconds,
    arrivalTimeMs: index * 500,
    presentVehicleIds: ['sequence-gap'],
    samples: [motionSample('sequence-gap', frame.x)],
  }))

  const sample = buffer.sample(1_000, 0.25)?.find((item) => item.id === 'sequence-gap')
  assert.ok(sample)
  assert.ok(sample.point[0] > 0 && sample.point[0] < 5)
  assert.equal(buffer.stats().compiledSegmentCount, 2)
})

test('continues output after consumed source frames are pruned', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 12; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.2,
      arrivalTimeMs: index * 200,
      samples: [motionSample('car', index * 2)],
    })
  }
  let previousX = Number.NEGATIVE_INFINITY
  for (let wallTimeMs = 2_400; wallTimeMs <= 4_000; wallTimeMs += 100) {
    if (wallTimeMs >= 2_600 && wallTimeMs % 200 === 0) {
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
  for (let index = 0; index <= 4; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: index <= 2 ? [motionSample('held', index)] : [],
    })
  }
  const held = buffer.sample(2_000)
  assert.ok(held?.some((sample) => sample.id === 'held'))
  for (let index = 5; index <= 12; index += 1) {
    buffer.push({ sceneGeneration: 0, sequence: index, elapsedSeconds: index * 0.5, arrivalTimeMs: index * 500, samples: [] })
  }
  let expired = null
  for (let wallTime = 2_100; wallTime <= 8_000; wallTime += 100) {
    expired = buffer.sample(wallTime)
  }
  assert.ok(expired && !expired.some((sample) => sample.id === 'held'))
})

test('hides an unresolved present vehicle instead of emitting a stale ghost pose', () => {
  const buffer = new VehicleMotionBuffer()
  let renderedFrames = 0
  let hiddenFrames = 0
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
      presentVehicleIds: ['stable-car'],
    })
    if (index < 5) continue
    const samples = buffer.sample(arrivalTimeMs + 24)
    if (!samples) continue
    renderedFrames += 1
    const vehicleSample = samples.find((sample) => sample.id === 'stable-car')
    if (!vehicleSample) hiddenFrames += 1
    else assert.notEqual(vehicleSample.sampleQuality, 'held')
  }
  assert.ok(renderedFrames > 900)
  assert.ok(hiddenFrames > 0)
  assert.deepEqual(buffer.stats().ghostVehicleIds, [])
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
  buffer.sample(4_000)
  const samples = buffer.sample(4_100)
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

test('adapts motion buffering and globally pauses the presentation clock on underrun', () => {
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
  assert.equal(buffer.stats().renderElapsedSeconds, beforeNextOutput)
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
  assert.equal(MAX_VEHICLE_OUTPUT_CATCH_UP_RATE, 1.05)
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
  const clock = new VehiclePresentationClock()
  clock.next(first.sampleWallTimeMs)
  const beforeLongFrame = clock.next(second.sampleWallTimeMs)
  const afterLongFrame = clock.next(recovered.sampleWallTimeMs)
  assert.ok(afterLongFrame - beforeLongFrame <= interval * MAX_VEHICLE_OUTPUT_CATCH_UP_RATE + 1e-9)
  assert.match(vehicleRendererSource, /presentationClock\.next\(sampleWallTimeMs\)/)
})

test('does not turn held recovery poses into authoritative motion keyframes', () => {
  const buffer = new VehicleMotionBuffer()
  const frames = [
    { elapsedSeconds: 0, x: 0, sampleQuality: 'authoritative' },
    { elapsedSeconds: 0.5, x: 0, sampleQuality: 'held' },
    { elapsedSeconds: 1, x: 10, sampleQuality: 'authoritative' },
    { elapsedSeconds: 1.5, x: 15, sampleQuality: 'authoritative' },
    { elapsedSeconds: 2, x: 20, sampleQuality: 'authoritative' },
    { elapsedSeconds: 2.5, x: 25, sampleQuality: 'authoritative' },
    { elapsedSeconds: 3, x: 30, sampleQuality: 'authoritative' },
  ]
  frames.forEach((frame, sequence) => buffer.push({
    sceneGeneration: 0,
    sequence,
    elapsedSeconds: frame.elapsedSeconds,
    arrivalTimeMs: frame.elapsedSeconds * 1_000,
    samples: [{ ...motionSample('recovering', frame.x), sampleQuality: frame.sampleQuality }],
  }))
  const samples = buffer.sample(3_000, 1.75)
  assert.ok(samples)
  assert.ok(samples[0].point[0] > 4.9, 'held pose incorrectly flattened the source curve')
})

test('isolates held identity frames without blocking the shared source horizon', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 4; index += 1) {
    buffer.push({
      sceneGeneration: 0, sequence: index, elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [{ ...motionSample('held-ready-boundary', index * 5), sampleQuality: 'authoritative' }],
    })
  }
  assert.equal(buffer.stats().compiledReadyElapsedSeconds, 2)
  buffer.push({
    sceneGeneration: 0, sequence: 5, elapsedSeconds: 2.5, arrivalTimeMs: 2_500,
    samples: [{ ...motionSample('held-ready-boundary', 20), sampleQuality: 'held' }],
  })
  assert.equal(buffer.stats().compiledReadyElapsedSeconds, 2.5)
  buffer.push({
    sceneGeneration: 0, sequence: 6, elapsedSeconds: 3, arrivalTimeMs: 3_000,
    samples: [{ ...motionSample('held-ready-boundary', 30), sampleQuality: 'authoritative' }],
  })
  assert.equal(buffer.stats().compiledReadyElapsedSeconds, 3)
})

test('compiles every authoritative interval once and reuses it for 1000 output queries', () => {
  const buffer = new VehicleMotionBuffer()
  const vehicleCount = 450
  for (let sequence = 0; sequence < 40; sequence += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence,
      elapsedSeconds: sequence * 0.1,
      arrivalTimeMs: sequence * 100,
      samples: Array.from({ length: vehicleCount }, (_, index) => (
        motionSample(`cache-${index}`, index * 0.001 + sequence * 0.000001)
      )),
    })
  }
  const compiled = buffer.stats().compiledSegmentCount
  assert.equal(compiled, (40 - 1) * vehicleCount)
  for (let output = 0; output < 1_000; output += 1) {
    assert.ok(buffer.sample(4_000 + output * (1_000 / 45), 1.95))
  }
  const stats = buffer.stats()
  assert.equal(stats.compiledSegmentCount, compiled)
  assert.ok(stats.compiledSegmentCacheHitCount >= 1_000 * vehicleCount)
  assert.ok(stats.compiledSegmentCacheHitRate > 0.95)
})

test('keeps valid vehicles moving when one vehicle timeline is incompatible', () => {
  const buffer = new VehicleMotionBuffer()
  for (let sequence = 0; sequence <= 8; sequence += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence,
      elapsedSeconds: sequence * 0.5,
      arrivalTimeMs: sequence * 500,
      samples: [
        motionSample('valid-timeline', sequence * 2),
        {
          ...motionSample('isolated-timeline', sequence * 2),
          sceneGeneration: 0,
          motionEpoch: sequence < 2 ? 0 : 1,
          sourceSpeedMetersPerSecond: 4,
        },
      ],
    })
  }
  const first = buffer.sample(4_000)
  const second = buffer.sample(4_100)
  assert.ok(first?.length && second?.length)
  const firstValid = first.find((sample) => sample.id === 'valid-timeline')
  const secondValid = second.find((sample) => sample.id === 'valid-timeline')
  const isolated = second.find((sample) => sample.id === 'isolated-timeline')
  assert.ok(firstValid && secondValid)
  assert.ok(secondValid.point[0] > firstValid.point[0])
  if (isolated) assert.notEqual(isolated.sampleQuality, 'held')
  assert.equal(buffer.stats().compiledReadyElapsedSeconds, 4)
  assert.deepEqual(buffer.stats().ghostVehicleIds, [])
})

test('retains B after an invalid A to B interval and independently recovers on later intervals', () => {
  const buffer = new VehicleMotionBuffer()
  const frames = [
    { elapsedSeconds: 0, x: 0, motionEpoch: 0 },
    { elapsedSeconds: 0.5, x: 5, motionEpoch: 1 },
    { elapsedSeconds: 1, x: 10, motionEpoch: 1 },
    { elapsedSeconds: 1.5, x: 15, motionEpoch: 1 },
    { elapsedSeconds: 2, x: 20, motionEpoch: 1 },
  ]
  frames.forEach((frame, sequence) => buffer.push({
    sceneGeneration: 0,
    sequence,
    elapsedSeconds: frame.elapsedSeconds,
    arrivalTimeMs: frame.elapsedSeconds * 1_000,
    presentVehicleIds: ['recover-after-invalid'],
    samples: [{
      ...motionSample('recover-after-invalid', frame.x),
      motionEpoch: frame.motionEpoch,
    }],
  }))
  assert.equal(buffer.sample(2_000, 0.25)?.find((sample) => sample.id === 'recover-after-invalid'), undefined)
  assert.equal(buffer.sample(2_050, 0.75)?.find((sample) => sample.id === 'recover-after-invalid'), undefined)
  const recovered = buffer.sample(2_100, 1.25)?.find((sample) => sample.id === 'recover-after-invalid')
  assert.ok(recovered)
  assert.ok(recovered.point[0] > 10 && recovered.point[0] < 15)
  assert.notEqual(recovered.sampleQuality, 'held')
  assert.deepEqual(buffer.stats().ghostVehicleIds, [])
})

test('globally slows and pauses on underrun instead of predicting individual vehicles', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 8; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [motionSample('global-clock', index * 5)],
    })
  }
  const first = buffer.sample(4_000)
  assert.ok(first?.length)
  const firstPosition = first[0].point[0]
  for (let wallTimeMs = 4_100; wallTimeMs <= 8_500; wallTimeMs += 100) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
  }
  const paused = buffer.sample(8_600)
  assert.ok(paused?.length)
  assert.ok(paused[0].point[0] >= firstPosition)
  assert.ok(buffer.stats().globalPlaybackRate <= 1e-6)
  assert.ok(buffer.stats().globalUnderrunPauseSeconds > 0)
  assert.doesNotMatch(vehicleRendererSource, /forwardCatchUp|waitingForAuthority/)
})

test('recovers the shared presentation clock at no more than 105 percent', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 8; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [motionSample('shared-recovery', index * 5)],
    })
  }
  buffer.sample(4_000)
  for (let wallTimeMs = 4_100; wallTimeMs <= 6_000; wallTimeMs += 100) buffer.sample(wallTimeMs)
  for (let index = 9; index <= 18; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: 6_000 + (index - 8) * 20,
      samples: [motionSample('shared-recovery', index * 5)],
    })
  }
  const recovered = buffer.sample(6_300)
  assert.ok(recovered?.length)
  assert.ok(buffer.stats().globalPlaybackRate <= buffer.stats().sourceRate * 1.05 + 1e-9)
})

test('preserves SUMO relative spacing on one authoritative interpolation clock', () => {
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 8; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [
        motionSample('leader', 30 + index * 2),
        motionSample('follower', 20 + index * 2),
      ],
    })
  }
  buffer.sample(4_000)
  const samples = buffer.sample(4_100)
  assert.ok(samples?.length === 2)
  const byId = new Map(samples.map((sample) => [sample.id, sample]))
  assert.ok(Math.abs(byId.get('leader').point[0] - byId.get('follower').point[0] - 10) < 1e-9)
  assert.ok(buffer.stats().authoritativeInterpolationCount >= 2)
})

/* legacy per-vehicle recovery scenarios are intentionally removed: the shared clock never
   extrapolates one vehicle independently or computes catch-up speed from position error. */
/*
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

test('waits for lagging authority indefinitely instead of ending recovery with a backward snap', () => {
  const metersPerLongitude = Math.cos(39 * Math.PI / 180) * 110_900
  const topologySample = (distanceMeters, speedMetersPerSecond = 10) => ({
    ...motionSample('authority-wait', 116 + distanceMeters / metersPerLongitude, 0),
    point: [116 + distanceMeters / metersPerLongitude, 39, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: 'authority-wait-path',
    segmentKey: 'authority-wait-lane',
    arcDistanceMeters: distanceMeters,
    pathArcDistanceMeters: distanceMeters,
    sourceSpeedMetersPerSecond: speedMetersPerSecond,
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
  for (let index = 0; index <= 5; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [topologySample(index * 5)],
    })
  }
  let previousArc = Number.NEGATIVE_INFINITY
  for (let wallTimeMs = 2_500; wallTimeMs <= 5_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const arc = Number(samples[0].pathArcDistanceMeters)
    assert.ok(arc + 1e-6 >= previousArc)
    previousArc = arc
  }
  buffer.push({
    sceneGeneration: 0,
    sequence: 6,
    elapsedSeconds: 5.5,
    arrivalTimeMs: 5_500,
    samples: [topologySample(25, 0)],
  })
  let waitingObserved = false
  for (let wallTimeMs = 5_550; wallTimeMs <= 9_000; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const arc = Number(samples[0].pathArcDistanceMeters)
    assert.ok(arc + 1e-6 >= previousArc, `vehicle moved backward from ${previousArc}m to ${arc}m`)
    previousArc = arc
    waitingObserved ||= samples[0].presentationState === 'waitingForAuthority'
  }
  const stats = buffer.stats()
  assert.equal(waitingObserved, true)
  assert.ok(stats.maximumWaitingForAuthoritySeconds > 2)
  assert.ok(stats.backwardCandidateInterceptCount > 0)
  assert.ok(stats.maximumBackwardCandidateMeters > 0)
  assert.equal(stats.visibleTeleportCount, 0)
})

test('joins authority ahead at a bounded forward rate after waiting without any backward frame', () => {
  const metersPerLongitude = Math.cos(39 * Math.PI / 180) * 110_900
  const sample = (distanceMeters, speedMetersPerSecond) => ({
    ...motionSample('authority-catch-up', 116 + distanceMeters / metersPerLongitude, 0),
    point: [116 + distanceMeters / metersPerLongitude, 39, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: 'catch-up-path',
    segmentKey: 'catch-up-lane',
    arcDistanceMeters: distanceMeters,
    pathArcDistanceMeters: distanceMeters,
    sourceSpeedMetersPerSecond: speedMetersPerSecond,
    transitionKind: 'same_lane',
    poseSource: 'topology',
    sampleQuality: 'authoritative',
    sceneGeneration: 0,
    motionEpoch: 0,
  })
  const buffer = new VehicleMotionBuffer()
  buffer.setMotionPathSampler({
    project(_key, coordinate) {
      return { pathArcDistanceMeters: (coordinate[0] - 116) * metersPerLongitude, distanceMeters: 0 }
    },
    sample(_key, distanceMeters) {
      return {
        longitude: 116 + distanceMeters / metersPerLongitude,
        latitude: 39,
        heading: 0,
        pathArcDistanceMeters: distanceMeters,
      }
    },
  })
  for (let index = 0; index <= 5; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [sample(index * 4, 8)],
    })
  }
  let previousArc = Number.NEGATIVE_INFINITY
  for (let wallTimeMs = 2_500; wallTimeMs <= 5_000; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    previousArc = Number(samples[0].pathArcDistanceMeters)
  }
  buffer.push({
    sceneGeneration: 0,
    sequence: 6,
    elapsedSeconds: 5,
    arrivalTimeMs: 5_000,
    samples: [sample(20, 0)],
  })
  for (let wallTimeMs = 5_050; wallTimeMs <= 6_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const arc = Number(samples[0].pathArcDistanceMeters)
    assert.ok(arc + 1e-6 >= previousArc)
    previousArc = arc
  }
  for (let wallTimeMs = 6_550; wallTimeMs <= 7_000; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const arc = Number(samples[0].pathArcDistanceMeters)
    assert.ok(arc + 1e-6 >= previousArc)
    previousArc = arc
  }
  buffer.push({
    sceneGeneration: 0,
    sequence: 7,
    elapsedSeconds: 7,
    arrivalTimeMs: 7_000,
    samples: [sample(60, 8)],
  })
  let maximumStep = 0
  for (let wallTimeMs = 7_050; wallTimeMs <= 9_000; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    const arc = Number(samples[0].pathArcDistanceMeters)
    maximumStep = Math.max(maximumStep, arc - previousArc)
    assert.ok(arc + 1e-6 >= previousArc)
    previousArc = arc
  }
  assert.ok(maximumStep <= 1.11, `forward catch-up step exceeded the 110% cap: ${maximumStep}m`)
  assert.ok(buffer.stats().maximumForwardCatchUpAccelerationMetersPerSecondSquared <= 3 + 1e-6)
})

test('blocks a backward raw-coordinate candidate along the current vehicle heading', () => {
  const metersPerLongitude = Math.cos(39 * Math.PI / 180) * 110_900
  const rawSample = (distanceMeters, speedMetersPerSecond = 6) => ({
    ...motionSample('raw-backward', 116 + distanceMeters / metersPerLongitude, 0),
    point: [116 + distanceMeters / metersPerLongitude, 39, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: `raw:edge:${distanceMeters}`,
    segmentKey: `raw:lane:${distanceMeters}`,
    sourceSpeedMetersPerSecond: speedMetersPerSecond,
    transitionKind: 'raw_fallback',
    roadTransitionKind: 'raw_continuous',
    poseSource: 'raw',
    sampleQuality: 'authoritative',
    sceneGeneration: 0,
    motionEpoch: 0,
  })
  const buffer = new VehicleMotionBuffer()
  for (let index = 0; index <= 4; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [rawSample(index * 3)],
    })
  }
  let previousLongitude = Number.NEGATIVE_INFINITY
  for (let wallTimeMs = 2_000; wallTimeMs <= 4_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    previousLongitude = samples[0].point[0]
  }
  buffer.push({
    sceneGeneration: 0,
    sequence: 5,
    elapsedSeconds: 4.5,
    arrivalTimeMs: 4_500,
    samples: [rawSample(8, 0)],
  })
  let waitingObserved = false
  for (let wallTimeMs = 4_550; wallTimeMs <= 6_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    assert.ok(samples?.length)
    assert.ok(samples[0].point[0] + 1e-12 >= previousLongitude)
    previousLongitude = samples[0].point[0]
    waitingObserved ||= samples[0].presentationState === 'waitingForAuthority'
  }
  assert.equal(waitingObserved, true)
  assert.ok(buffer.stats().backwardCandidateInterceptCount > 0)
  assert.equal(buffer.stats().visibleTeleportCount, 0)
})

test('does not re-control SUMO center gaps while prediction only moves forward', () => {
  const metersPerLongitude = Math.cos(39 * Math.PI / 180) * 110_900
  const sample = (id, arcDistanceMeters, speedMetersPerSecond) => ({
    ...motionSample(id, 116 + arcDistanceMeters / metersPerLongitude, 0),
    point: [116 + arcDistanceMeters / metersPerLongitude, 39, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: 'shared-lane',
    segmentKey: 'shared-lane',
    occupancyKey: 'lane:shared-lane',
    arcDistanceMeters,
    pathArcDistanceMeters: arcDistanceMeters,
    sourceSpeedMetersPerSecond: speedMetersPerSecond,
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
      return {
        longitude: 116 + distanceMeters / metersPerLongitude,
        latitude: 39,
        heading: 0,
        pathArcDistanceMeters: distanceMeters,
      }
    },
  })
  for (let index = 0; index <= 3; index += 1) {
    const elapsedSeconds = index * 0.5
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds,
      arrivalTimeMs: elapsedSeconds * 1_000,
      samples: [
        sample('leader', 30 + elapsedSeconds * 2, 2),
        sample('follower', 20 + elapsedSeconds * 6, 6),
      ],
    })
  }
  let previousFollowerArc = Number.NEGATIVE_INFINITY
  let latest = null
  for (let wallTimeMs = 1_500; wallTimeMs <= 4_500; wallTimeMs += 50) {
    const samples = buffer.sample(wallTimeMs)
    if (!samples?.length) continue
    latest = new Map(samples.map((item) => [item.id, item]))
    const followerArc = Number(latest.get('follower').arcDistanceMeters)
    assert.ok(followerArc + 1e-6 >= previousFollowerArc)
    previousFollowerArc = followerArc
  }
  assert.ok(latest)
  assert.equal(buffer.stats().backwardCandidateInterceptCount, 0)
  assert.equal(buffer.stats().visibleTeleportCount, 0)
})

*/

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
  assert.equal(buffer.stats().incompatiblePathInterpolationCount, 0)
  assert.ok(buffer.stats().incompatiblePathInterpolationBlockedCount > 0)
  assert.ok(buffer.stats().rejectedCompiledSegmentCount > 0)
})

test('rejects a detailed lane change when target-lane Frenet projection is unavailable', () => {
  const left = {
    ...motionSample('lane-change-no-projection', 116, 0),
    point: [116, 39, 1.1], motionPathKey: 'lane:left', pathArcDistanceMeters: 2,
    detailedCorridorValidation: true, vehicleLengthMeters: 5, vehicleWidthMeters: 1.8,
    time: 0,
  }
  const right = {
    ...left,
    point: [116.00005, 39.00003, 1.1], motionPathKey: 'lane:right', pathArcDistanceMeters: 7,
    transitionKind: 'lane_change', roadTransitionKind: 'lane_change',
    laneChangeCorridorKey: 'lane:left->lane:right',
    corridorMotionPathKeys: ['lane:left', 'lane:right'], time: 500,
  }
  const unresolved = interpolateVehicleTwinSample(left, right, 0.5, {
    project: () => null,
    sample: () => null,
    containsVehicle: () => true,
  })
  assert.equal(unresolved.intermediatePoseValid, false)
  assert.equal(unresolved.intermediateValidationReason, 'lane_change_path_unresolved')
})

test('validates compiled segment endpoints with the same road-corridor rule', () => {
  const leftSample = {
    ...motionSample('endpoint-validation', 116, 0),
    point: [116, 39, 1.1], motionPathKey: 'lane:only', pathArcDistanceMeters: 0,
    detailedCorridorValidation: true, vehicleLengthMeters: 5, vehicleWidthMeters: 1.8,
    time: 0, sceneGeneration: 0, motionEpoch: 0,
  }
  const rightSample = {
    ...leftSample,
    point: [116.00005, 39, 1.1], pathArcDistanceMeters: 5, time: 500,
  }
  const segment = compileMotionSegment(
    { frame: { sceneGeneration: 0, sequence: 0, elapsedSeconds: 0, arrivalTimeMs: 0, samples: [leftSample] }, sample: leftSample },
    { frame: { sceneGeneration: 0, sequence: 1, elapsedSeconds: 0.5, arrivalTimeMs: 500, samples: [rightSample] }, sample: rightSample },
    {
      project: (_key, coordinate) => ({ pathArcDistanceMeters: (coordinate[0] - 116) * 100_000, distanceMeters: 0 }),
      sample: (_key, distance) => ({ longitude: 116 + distance / 100_000, latitude: 39, heading: 0, pathArcDistanceMeters: distance }),
      containsVehicle: (_keys, coordinate) => coordinate[0] < 116.00005 - 1e-9,
    },
  )
  assert.equal(segment.valid, false)
  assert.match(segment.rejectionReason, /^endpoint:/)
})

test('uses buffered live-via evidence to compile a dynamic event vehicle turn', () => {
  const leftSample = {
    ...motionSample('event_vehicle_opening_000001', 116, 0),
    point: [116, 39, 1.1], motionPathKey: 'lane:incoming', pathArcDistanceMeters: 0,
    detailedCorridorValidation: true, vehicleLengthMeters: 5, vehicleWidthMeters: 1.8,
    time: 0, sceneGeneration: 0, motionEpoch: 0,
  }
  const rightSample = {
    ...leftSample,
    point: [116.00005, 39, 1.1], motionPathKey: 'route:tls:7', pathArcDistanceMeters: 5,
    transitionKind: 'topological', roadTransitionKind: 'topology_successor',
    motionPathBridgeKey: 'lane:incoming->route:tls:7',
    corridorMotionPathKeys: ['lane:incoming', 'route:tls:7'],
    dynamicConnectionEvidence: {
      source: 'live_via', connectionKey: 'tls:7', observedLaneId: ':via_0',
      fromLaneId: 'incoming_0', toLaneId: 'outgoing_0', viaLaneIds: [':via_0'],
    },
    time: 500,
  }
  const sampler = {
    project: (_key, coordinate) => ({ pathArcDistanceMeters: (coordinate[0] - 116) * 100_000, distanceMeters: 0 }),
    sample: (_key, distance) => ({ longitude: 116 + distance / 100_000, latitude: 39, heading: 0, pathArcDistanceMeters: distance }),
    containsVehicle: () => true,
  }
  const segment = compileMotionSegment(
    { frame: { sceneGeneration: 0, sequence: 0, elapsedSeconds: 0, arrivalTimeMs: 0, samples: [leftSample] }, sample: leftSample },
    { frame: { sceneGeneration: 0, sequence: 1, elapsedSeconds: 0.5, arrivalTimeMs: 500, samples: [rightSample] }, sample: rightSample },
    sampler,
  )
  assert.equal(segment.valid, true)
  assert.equal(segment.routeSource, 'buffered_lookahead')
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

test('reserves the visibility budget for vehicles in the active detailed intersection', () => {
  const selector = new StableVehicleSelector()
  const center = [116, 39]
  const outer = [
    vehicle('outer-a', 116.0001, 39),
    vehicle('outer-b', 116.0002, 39),
  ]
  selector.select(outer, (coordinate) => [...coordinate], center, 500, 's:1', 2)
  const selected = selector.select([
    ...outer,
    vehicle('local', 116.0003, 39),
  ], (coordinate) => [...coordinate], center, 500, 's:2', 2, (item) => (
    item.vehicle.vehicle_id === 'local'
  ))
  assert.equal(selected.length, 2)
  assert.ok(selected.some((item) => item.vehicle.vehicle_id === 'local'))
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

test('paces Twin output while keeping source, viewport, and selected rosters separate', () => {
  assert.equal(VEHICLE_TWIN_RENDER_DELAY_MS, 500)
  assert.equal(VEHICLE_TWIN_PRIME_SPACING_MS, 50)
  assert.doesNotMatch(vehicleRendererSource, /SOURCE_MISSING_GRACE_SNAPSHOTS/)
  assert.match(vehicleRendererSource, /NORMAL_OUTPUT_FRAME_MS = 1_000 \/ NORMAL_TWIN_OUTPUT_FPS/)
  assert.doesNotMatch(vehicleRendererSource, /filterSurfaceRenderableVehicles/)
  assert.match(vehicleRendererSource, /VIEWPORT_SNAPSHOT_HISTORY_SECONDS = 30/)
  assert.match(vehicleRendererSource, /this\.replayingViewportSnapshots = true/)
  assert.match(vehicleRendererSource, /CONSTRAINED_OUTPUT_FRAME_MS = 1_000 \/ STABLE_TWIN_OUTPUT_FPS/)
  assert.match(vehicleRendererSource, /this\.twinPlaybackBacklogMs = pacing\.backlogMs/)
  assert.match(vehicleRendererSource, /this\.pushMotionFrameAt\(pacing\.sampleWallTimeMs, wallTimeMs\)/)
  assert.match(vehicleRendererSource, /recordSourceRoster\(context\.sequence/)
  assert.match(vehicleRendererSource, /!lanePose\s*&& this\.lanePoseResolver\?\.hasLane\(vehicle\.lane_id\)/)
  assert.doesNotMatch(vehicleRendererSource, /rejectedInsideDetailedLane[\s\S]{0,160}coversDetailedArea/)
  assert.match(vehicleRendererSource, /sourceVehicleIds,/)
  assert.match(vehicleRendererSource, /viewportVehicleIds,/)
  assert.match(vehicleRendererSource, /selectedVehicleIds: visible\.map/)
  assert.doesNotMatch(vehicleRendererSource, /presentVehicleIds:/)
  assert.doesNotMatch(vehicleRendererSource, /retainMissingSourceSamples/)
  assert.doesNotMatch(vehicleRendererSource, /emptyRenderStreak \+= 1/)
  assert.match(vehicleRendererSource, /!isVehicleAnimationActive\(this\.lastContext\.state\)/)
  assert.match(
    vehicleRendererSource,
    /result\.status === 'waiting'[\s\S]{0,320}this\.twinPresenter\.freezeAfterVisible\(\)/,
  )
  assert.match(
    vehicleRendererSource,
    /result\.status === 'selection_empty'[\s\S]{0,180}this\.twinPresenter\.freezeAfterVisible\(\)/,
  )
})

test('freezes a non-empty terminal Twin roster and only clears an authoritative empty one', () => {
  assert.match(vehicleRendererSource, /context\.state === 'STOPPED'/)
  assert.match(vehicleRendererSource, /context\.state === 'COMPLETED'/)
  assert.match(vehicleRendererSource, /context\.state === 'FAILED'/)
  const terminalEmptyBlock = vehicleRendererSource.slice(
    vehicleRendererSource.indexOf('vehicles.length === 0'),
    vehicleRendererSource.indexOf('const renderableVehicles'),
  )
  assert.match(terminalEmptyBlock, /this\.twinPresenter\.reset\('terminal_authoritative_empty'\)/)
  assert.match(vehicleRendererSource, /this\.twinPresenter\.freezeAfterVisible\(\)/)
})

test('reproduces MapV window exhaustion with zero delay and two 50 ms samples', () => {
  const probe = new MapvTwinWindowProbe(0)
  probe.push(950, 1_000)
  probe.push(1_000, 1_000)
  assert.equal(probe.tick(1_000), true)
  assert.equal(probe.tick(1_050), false)
})

test('keeps the warming Twin sample window alive until MapV produces real instances', () => {
  const warmupBlock = vehicleTwinPresenterSource.slice(
    vehicleTwinPresenterSource.indexOf('private scheduleWarmupRender'),
    vehicleTwinPresenterSource.indexOf('private cancelWarmupRender'),
  )
  assert.match(warmupBlock, /channel\.warmupSamples\.map/)
  assert.match(warmupBlock, /this\.pushToChannel\(channel, samples\)/)
  assert.match(warmupBlock, /channel\.actualVisibleCount > 0/)
})

for (const fps of [24, 30]) {
  test(`keeps the MapV two-sample window alive at ${fps} fps across a 250 ms long frame`, () => {
    const probe = new MapvTwinWindowProbe(VEHICLE_TWIN_RENDER_DELAY_MS)
    probe.push(950, 1_000)
    probe.push(1_000, 1_000)
    const outputIntervalMs = 1_000 / fps
    let nextOutputMs = 1_000 + outputIntervalMs
    for (let wallTimeMs = 1_000; wallTimeMs <= 3_000; wallTimeMs += 1_000 / 60) {
      const inLongFrame = wallTimeMs >= 1_600 && wallTimeMs < 1_850
      if (!inLongFrame && wallTimeMs + 1e-6 >= nextOutputMs) {
        probe.push(wallTimeMs, wallTimeMs)
        nextOutputMs = wallTimeMs + outputIntervalMs
      }
      const visible = probe.tick(wallTimeMs)
      if (wallTimeMs >= 1_500) assert.equal(visible, true, `window exhausted at ${wallTimeMs}`)
    }
    assert.ok(probe.depthMs() > 0)
  })
}

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

test('does not override authoritative SUMO positions with visual queue rules', () => {
  assert.doesNotMatch(vehicleRendererSource, /resolveVisualQueueConstraints/)
  assert.doesNotMatch(vehicleRendererSource, /minimumModelCenterDistanceMeters/)
  assert.doesNotMatch(vehicleRendererSource, /maximumModelCenterDistanceMeters/)
  assert.doesNotMatch(vehicleRendererSource, /MAX_VISUAL_BACKTRACK_METERS/)
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

test('removes the prediction queue controller that could pull vehicles backward', async () => {
  const motionBufferSource = await readFile(
    new URL('../src/mapv/vehicleMotionBuffer.ts', import.meta.url),
    'utf8',
  )
  assert.doesNotMatch(motionBufferSource, /enforcePredictionQueueConstraints/)
  assert.doesNotMatch(motionBufferSource, /predicted_queue_spacing/)
  assert.doesNotMatch(motionBufferSource, /authoritative_headway_cap/)
  assert.doesNotMatch(motionBufferSource, /authoritativeLeaderId/)
  assert.doesNotMatch(motionBufferSource, /authoritativeLeaderGapMeters/)
  assert.doesNotMatch(motionBufferSource, /applyPredictionForwardCaps/)
})

test('uses a speed-aware outlier gate for same-lane positions', () => {
  const previous = { longitude: 116, latitude: 39, elapsedSeconds: 10 }
  assert.equal(maximumStableVehicleDisplacementMeters(0, 1), 2)
  assert.equal(maximumStableVehicleDisplacementMeters(10, 1), 13.5)
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

test('keeps stop-boundary state diagnostic-only after a vehicle crosses it', () => {
  const first = { ...vehicle('car', 116, 39), speed: 0, distance: 10, route_id: 'route-a', route_index: 0 }
  assert.equal(resolveCrossedStopLine(null, first, 12, 10, false), true)
  const crossed = {
    telemetryReliable: true, backendDistance: 10, routeId: 'route-a', routeIndex: 0, laneId: first.lane_id,
    trackKey: 'lane', trackDistanceMeters: 10, crossedStopLine: true,
    laneResolutionFailures: 0, longitude: 116, latitude: 39, heading: 0,
    elapsedSeconds: 1, motionEpoch: 0, lastSeenSequence: 1,
  }
  assert.equal(resolveCrossedStopLine(crossed, first, 9, 10, true), true)
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
  const expected = samplePolyline(lane.vehicleGuidePoints, 0.5)
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

test('maps demo_1 SUMO lateral offsets onto rebuilt visual lanes continuously', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_1/manifest.json', import.meta.url),
    'utf8',
  ))
  const resolver = createIntersectionLanePoseResolver(manifest, projectSimulationCoordinateToBaiduMap)
  for (const laneId of ['-56915_1', '-52436_0', '-46724_0', '-47022.97_0']) {
    const lane = manifest.edges.flatMap((edge) => edge.lanes).find((item) => item.id === laneId)
    assert.ok(lane, `${laneId} is missing from demo_1`)
    const progress = 0.55
    const sourcePoint = samplePolyline(lane.points, progress)
    const sourceHeading = tangentAtProgress(lane.points, progress)
    const sourceLength = lane.points.slice(1).reduce((total, point, index) => (
      total + Math.hypot(point[0] - lane.points[index][0], point[1] - lane.points[index][1])
    ), 0)
    const renderLength = lane.vehicleGuidePoints.slice(1).reduce((total, point, index) => (
      total + Math.hypot(point[0] - lane.vehicleGuidePoints[index][0], point[1] - lane.vehicleGuidePoints[index][1])
    ), 0)
    const renderStartProjection = nearestPolylineProgress(lane.vehicleGuidePoints[0], lane.points)
    const renderEndProjection = nearestPolylineProgress(lane.vehicleGuidePoints.at(-1), lane.points)
    assert.ok(renderStartProjection)
    assert.ok(renderEndProjection)
    const sourceRenderStart = Math.min(
      renderStartProjection.progress * sourceLength,
      renderEndProjection.progress * sourceLength,
    )
    const sourceRenderEnd = Math.max(
      renderStartProjection.progress * sourceLength,
      renderEndProjection.progress * sourceLength,
    )
    const sourceRenderSpan = sourceRenderEnd - sourceRenderStart
    const sourceDistance = progress * sourceLength
    const renderProgress = mapSourceProgressToRenderDistance(
      (sourceDistance - sourceRenderStart) / sourceRenderSpan,
      sourceRenderSpan,
      renderLength,
    ) / renderLength
    const renderPoint = samplePolyline(lane.vehicleGuidePoints, renderProgress)
    const renderHeading = tangentAtProgress(lane.vehicleGuidePoints, renderProgress)
    assert.notEqual(sourceHeading, null)
    assert.notEqual(renderHeading, null)
    const lateralOffsetMeters = 0.8
    const lateralOffsetScene = lateralOffsetMeters * manifest.horizontalScale
    const sourceWithOffset = [
      sourcePoint[0] - Math.sin(sourceHeading) * lateralOffsetScene,
      sourcePoint[1] + Math.cos(sourceHeading) * lateralOffsetScene,
    ]
    const coordinate = unprojectWebMercatorToBd09([
      manifest.origin.webMercator[0] + sourceWithOffset[0],
      manifest.origin.webMercator[1] + sourceWithOffset[1],
    ])
    const pose = resolver(laneId, coordinate, 0, undefined, undefined, {
      speedMetersPerSecond: 5,
      expectedHeading: sourceHeading,
      preserveSourceLateralOffset: true,
    })
    assert.ok(pose, `${laneId} did not map`)
    const mapped = projectBd09ToWebMercator([pose.longitude, pose.latitude])
    const expected = [
      manifest.origin.webMercator[0] + renderPoint[0] - Math.sin(renderHeading) * lateralOffsetScene,
      manifest.origin.webMercator[1] + renderPoint[1] + Math.cos(renderHeading) * lateralOffsetScene,
    ]
    const mappingErrorMeters = Math.hypot(mapped[0] - expected[0], mapped[1] - expected[1])
      / manifest.horizontalScale
    assert.ok(mappingErrorMeters < 0.15, `${laneId} mapping error ${mappingErrorMeters}m`)
    assert.ok(Math.abs(pose.sourceLateralOffsetMeters - lateralOffsetMeters) < 0.02)
    assert.equal(pose.mappingMode, 'source_lateral')
    const nextProgress = progress + 5 * manifest.horizontalScale / sourceLength
    const nextSourcePoint = samplePolyline(lane.points, nextProgress)
    const nextSourceHeading = tangentAtProgress(lane.points, nextProgress)
    assert.notEqual(nextSourceHeading, null)
    const nextCoordinate = unprojectWebMercatorToBd09([
      manifest.origin.webMercator[0] + nextSourcePoint[0]
        - Math.sin(nextSourceHeading) * lateralOffsetScene,
      manifest.origin.webMercator[1] + nextSourcePoint[1]
        + Math.cos(nextSourceHeading) * lateralOffsetScene,
    ])
    const nextPose = resolver(laneId, nextCoordinate, 0, undefined, undefined, {
      speedMetersPerSecond: 5,
      expectedHeading: nextSourceHeading,
      preserveSourceLateralOffset: true,
    })
    assert.ok(nextPose, `${laneId} next authoritative pose did not map`)
    const nextMapped = projectBd09ToWebMercator([nextPose.longitude, nextPose.latitude])
    const mappedGapMeters = Math.hypot(
      nextMapped[0] - mapped[0],
      nextMapped[1] - mapped[1],
    ) / manifest.horizontalScale
    assert.ok(
      Math.abs(mappedGapMeters - 5) <= 0.15,
      `${laneId} changed an authoritative 5m gap to ${mappedGapMeters}m`,
    )
  }
  assert.match(vehicleRendererSource, /sourceDistanceToLaneCenterMeters <= 0\.35/)
})

test('keeps an internal SUMO turn lane on its visual connection curve', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_2/manifest.json', import.meta.url),
    'utf8',
  ))
  const connection = (manifest.vehicleConnections ?? manifest.connections)
    .find((item) => item.direction === 'l' && item.viaPoints?.length >= 4)
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
    for (const connection of manifest.vehicleConnections ?? manifest.connections) {
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

test('keeps all 421 SUMO vehicle connections path-distinct and rejects conflicting visual tangents', async () => {
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
    for (const connection of manifest.vehicleConnections ?? manifest.connections) {
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
  assert.equal(connectionCount, 421)
  assert.ok(ambiguousOutgoingLaneCount >= 115)
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

test('keeps every 24, 30, and 45 fps lane-change frame inside the adjacent-lane corridor', () => {
  const origin = { longitude: 116, latitude: 39 }
  const originPlane = projectBd09ToWebMercator([origin.longitude, origin.latitude])
  const manifest = {
    origin: { ...origin, webMercator: originPlane },
    horizontalScale: 1,
    radiusMeters: 100,
    junctionShape: [[-5, -5], [25, -5], [25, 9], [-5, 9]],
    edges: [{
      id: 'edge',
      incoming: false,
      lanes: [
        { id: 'edge_0', index: 0, width: 3.5, widthMeters: 3.5, speed: 12, points: [[0, 0], [20, 0]], renderPoints: [[0, 0], [20, 0]] },
        { id: 'edge_1', index: 1, width: 3.5, widthMeters: 3.5, speed: 12, points: [[0, 3.5], [20, 3.5]], renderPoints: [[0, 3.5], [20, 3.5]] },
      ],
    }],
    connections: [],
  }
  const resolver = createIntersectionLanePoseResolver(manifest, (coordinate) => coordinate)
  const coordinate = (point) => unprojectWebMercatorToBd09([
    originPlane[0] + point[0],
    originPlane[1] + point[1],
  ])
  const leftPose = resolver('edge_0', coordinate([5, 0]), 2.5, undefined, undefined, {
    speedMetersPerSecond: 10,
    expectedHeading: 0,
    preserveSourceLateralOffset: true,
    vehicleHalfLengthMeters: 2.5,
    vehicleHalfWidthMeters: 0.9,
  })
  assert.ok(leftPose)
  const rightPose = resolver('edge_1', coordinate([10, 3.5]), 2.5, 'edge_0', leftPose.trackKey, {
    speedMetersPerSecond: 10,
    expectedHeading: 0,
    preserveSourceLateralOffset: true,
    vehicleHalfLengthMeters: 2.5,
    vehicleHalfWidthMeters: 0.9,
  })
  assert.ok(rightPose)
  assert.equal(rightPose.transitionKind, 'lane_change')
  const left = {
    ...motionSample('disturbance-lane-change', leftPose.longitude, 0),
    point: [leftPose.longitude, leftPose.latitude, 1.1],
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    motionPathKey: leftPose.motionPathKey,
    pathArcDistanceMeters: leftPose.pathArcDistanceMeters,
    sourceLateralOffsetMeters: leftPose.sourceLateralOffsetMeters,
    sourceSpeedMetersPerSecond: 10,
    vehicleLengthMeters: 5,
    vehicleWidthMeters: 1.8,
    detailedCorridorValidation: true,
    time: 0,
  }
  const right = {
    ...left,
    point: [rightPose.longitude, rightPose.latitude, 1.1],
    motionPathKey: rightPose.motionPathKey,
    pathArcDistanceMeters: rightPose.pathArcDistanceMeters,
    sourceLateralOffsetMeters: rightPose.sourceLateralOffsetMeters,
    transitionKind: 'lane_change',
    roadTransitionKind: 'lane_change',
    laneChangeCorridorKey: `${leftPose.motionPathKey}->${rightPose.motionPathKey}`,
    corridorMotionPathKeys: [leftPose.motionPathKey, rightPose.motionPathKey],
    time: 500,
  }
  for (const fps of [24, 30, 45]) {
    for (let frame = 0; frame <= Math.ceil(fps * 0.5); frame += 1) {
      const ratio = Math.min(1, frame / (fps * 0.5))
      const sample = interpolateVehicleTwinSample(
        left,
        right,
        ratio,
        resolver.motionPathSampler,
      )
      assert.notEqual(sample.intermediatePoseValid, false, `${fps} fps frame ${frame}`)
      assert.equal(resolver.motionPathSampler.containsVehicle(
        [leftPose.motionPathKey, rightPose.motionPathKey],
        [sample.point[0], sample.point[1]],
        sample.vehicleHeading,
        2.5,
        0.9,
      ), true, `${fps} fps OBB frame ${frame}`)
    }
  }
})

test('rejects an invalid lane-change segment before playback instead of holding then jumping', () => {
  const buffer = new VehicleMotionBuffer()
  const sampler = {
    project: (key, coordinate) => ({
      pathArcDistanceMeters: (coordinate[0] - 116) * 100_000,
      distanceMeters: 0,
    }),
    sample: (key, distanceMeters) => ({
      longitude: 116 + distanceMeters / 100_000,
      latitude: key === 'lane:right' ? 39.00003 : 39,
      heading: 0,
      pathArcDistanceMeters: distanceMeters,
    }),
    containsVehicle: (_keys, coordinate) => coordinate[0] <= 116.000222,
  }
  buffer.setMotionPathSampler(sampler)
  for (let index = 0; index <= 4; index += 1) {
    buffer.push({
      sceneGeneration: 0,
      sequence: index,
      elapsedSeconds: index * 0.5,
      arrivalTimeMs: index * 500,
      samples: [{
        ...motionSample('corridor-hold', 116 + index * 0.00005, 0),
        point: [116 + index * 0.00005, 39, 1.1],
        motionPathKey: 'lane:left',
        pathArcDistanceMeters: index * 5,
        vehicleHeading: 0,
        modelForwardAxisAngle: 0,
        sourceSpeedMetersPerSecond: 10,
        vehicleLengthMeters: 5,
        vehicleWidthMeters: 1.8,
        detailedCorridorValidation: true,
        sampleQuality: 'authoritative',
        motionEpoch: 0,
      }],
    })
  }
  buffer.push({
    sceneGeneration: 0,
    sequence: 5,
    elapsedSeconds: 2.5,
    arrivalTimeMs: 2_500,
    samples: [{
      ...motionSample('corridor-hold', 116.00025, 0),
      point: [116.00025, 39.00003, 1.1],
      motionPathKey: 'lane:right',
      pathArcDistanceMeters: 25,
      vehicleHeading: 0,
      modelForwardAxisAngle: 0,
      sourceSpeedMetersPerSecond: 10,
      vehicleLengthMeters: 5,
      vehicleWidthMeters: 1.8,
      detailedCorridorValidation: true,
      transitionKind: 'lane_change',
      roadTransitionKind: 'lane_change',
      laneChangeCorridorKey: 'lane:left->lane:right',
      corridorMotionPathKeys: ['lane:left', 'lane:right'],
      sampleQuality: 'authoritative',
      motionEpoch: 0,
    }],
  })
  const positions = []
  for (let wallTime = 2_500; wallTime <= 4_500; wallTime += 50) {
    const sample = buffer.sample(wallTime)?.find((item) => item.id === 'corridor-hold')
    if (sample) positions.push(sample.point[0])
  }
  assert.ok(positions.length > 20)
  assert.ok(positions.every((position, index) => (
    index === 0 || position + 1e-12 >= positions[index - 1]
  )))
  assert.ok(buffer.stats().laneChangeCorridorViolationCount > 0)
  assert.ok(buffer.stats().rejectedCompiledSegmentCount > 0)
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

test('maps legacy source progress linearly without a local speed peak', () => {
  for (const [sourceLength, renderLength] of [[10, 8.4], [10, 9.3], [10, 10.8]]) {
    const distances = Array.from({ length: 101 }, (_, index) => (
      mapSourceProgressToRenderDistance(index / 100, sourceLength, renderLength)
    ))
    assert.ok(distances.every((distance, index) => index === 0 || distance >= distances[index - 1]))
    const steps = distances.slice(1).map((distance, index) => distance - distances[index])
    assert.ok(steps.every((step) => Math.abs(step - steps[0]) < 1e-9))
    assert.equal(distances.at(-1), renderLength)
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

test('exposes the stop boundary without changing an authoritative SUMO pose', async () => {
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
  const pose = resolver(
    lane.id,
    coordinate,
    CAR_MODEL_PROFILE.targetLengthMeters / 2,
    undefined,
    undefined,
    { speedMetersPerSecond: 0 },
  )
  assert.ok(pose)
  assert.equal(pose.stopClamped, false)
  assert.ok(Number.isFinite(pose.stopFrontLimitDistanceMeters))
  assert.ok(Math.abs(pose.naturalFrontDistanceMeters - pose.stopFrontLimitDistanceMeters) <= 0.05 + 1e-9)
  assert.equal(visualStopFrontLimitDistance(10, 1), 10)
})

test('never rewrites authoritative entry-lane positions across all 20 intersections', async () => {
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
      const pose = resolver(
        lane.id,
        coordinate,
        CAR_MODEL_PROFILE.targetLengthMeters / 2,
        undefined,
        undefined,
        { speedMetersPerSecond: 0 },
      )
      assert.ok(pose, `demo_${index}:${lane.id} did not resolve`)
      assert.equal(pose.stopClamped, false, `demo_${index}:${lane.id} was moved by the frontend`)
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

test('distinguishes motion buffering from an authoritative empty roster', () => {
  const waitingBuffer = new VehicleMotionBuffer()
  waitingBuffer.push({
    sceneGeneration: 0,
    sequence: 1,
    elapsedSeconds: 10,
    arrivalTimeMs: 10_000,
    samples: [motionSample('waiting', 1)],
    presentVehicleIds: ['waiting'],
  })
  assert.deepEqual(waitingBuffer.sampleResult(10_000, 8), {
    status: 'waiting',
    reason: 'insufficient_frames',
    displayElapsedSeconds: 8,
    sourceVehicleCount: 0,
    viewportVehicleCount: 0,
    selectedVehicleCount: 0,
    authoritativeVehicleCount: 0,
    unresolvedVehicleCount: 0,
    samples: [],
  })

  const emptyBuffer = new VehicleMotionBuffer()
  emptyBuffer.push({
    sceneGeneration: 0, sequence: 1, elapsedSeconds: 10, arrivalTimeMs: 10_000,
    samples: [], presentVehicleIds: [],
  })
  emptyBuffer.push({
    sceneGeneration: 0, sequence: 2, elapsedSeconds: 10.5, arrivalTimeMs: 10_500,
    samples: [], presentVehicleIds: [],
  })
  const empty = emptyBuffer.sampleResult(10_500, 10.25)
  assert.equal(empty.status, 'authoritative_empty')
  assert.equal(empty.authoritativeVehicleCount, 0)
})

test('does not classify an empty camera selection as an authoritative empty roster', () => {
  const buffer = new VehicleMotionBuffer()
  for (const [sequence, elapsedSeconds] of [[1, 10], [2, 10.5]]) {
    buffer.push({
      sceneGeneration: 0,
      sequence,
      elapsedSeconds,
      arrivalTimeMs: elapsedSeconds * 1_000,
      samples: [],
      sourceVehicleIds: ['global', 'local'],
      viewportVehicleIds: ['local'],
      selectedVehicleIds: [],
    })
  }
  const result = buffer.sampleResult(10_500, 10.25)
  assert.equal(result.status, 'selection_empty')
  assert.equal(result.sourceVehicleCount, 2)
  assert.equal(result.viewportVehicleCount, 1)
  assert.equal(result.selectedVehicleCount, 0)
})

test('notifies the renderer when a compiled interval becomes available', () => {
  const buffer = new VehicleMotionBuffer()
  const events = []
  buffer.setCompilationReadyListener((event) => events.push(event))
  buffer.push({
    sceneGeneration: 0, sequence: 1, elapsedSeconds: 0, arrivalTimeMs: 0,
    samples: [motionSample('compiled-notification', 0)],
  })
  buffer.push({
    sceneGeneration: 0, sequence: 2, elapsedSeconds: 0.5, arrivalTimeMs: 500,
    samples: [motionSample('compiled-notification', 1)],
  })
  assert.ok(events.length >= 1)
  assert.equal(events.at(-1).pendingCount, 0)
  assert.equal(buffer.sampleResult(500, 0.25).status, 'ready')
})
