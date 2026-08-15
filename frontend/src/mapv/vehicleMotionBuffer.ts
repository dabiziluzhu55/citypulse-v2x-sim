import type { DynamicVehicleRouteSource, VehicleTwinSample } from './vehicleTwinSample'
import {
  MAX_MOTION_PATH_HEADING_DELTA,
  type MotionPathSampler,
} from './realistic/intersectionLaneHeading.ts'

export const MIN_VEHICLE_BUFFER_SECONDS = 2
export const MAX_VEHICLE_BUFFER_SECONDS = 3
const BUFFER_INTERVAL_MULTIPLIER = 2
const MAX_OUTPUT_STEP_SECONDS = 0.25
const MAX_TIMING_SAMPLES = 30
const ROBUST_RATE_SAMPLE_COUNT = 9
const MAX_SOURCE_FRAMES = 40
const MAX_TRACKED_SOURCE_GAP_MS = 10_000
const MIN_RATE_INTERVAL_MS = 100
const MAX_PLAYBACK_RATE_RATIO = 1.05
const UNDERRUN_SLOWDOWN_WINDOW_SECONDS = 0.75
const MAX_INTERPOLATED_SPEED_RATIO = 1.05
const MAX_SYNTHETIC_ACCELERATION_MPS2 = 3
const COMPILED_SEGMENT_VALIDATION_FPS = 45

export interface AuthoritativeVehicleFrame {
  sceneGeneration: number
  sequence: number
  elapsedSeconds: number
  arrivalTimeMs: number
  samples: VehicleTwinSample[]
  presentVehicleIds?: readonly string[]
}

export type VehicleMotionSourceFrame = AuthoritativeVehicleFrame

export interface CompiledMotionSegment {
  key: string
  vehicleId: string
  startElapsedSeconds: number
  endElapsedSeconds: number
  valid: boolean
  validationSampleCount: number
  routeSource: DynamicVehicleRouteSource
  rejectionReason?: string
  recoveryRequired?: boolean
  playable?: boolean
  consecutiveValidCount?: number
}
export type CompiledVehicleSegment = CompiledMotionSegment

export type VehicleSegmentState = 'ready' | 'isolated' | 'recovering'

export interface VehiclePresenceInterval {
  vehicleId: string
  enteredElapsedSeconds: number
  exitedElapsedSeconds: number | null
}

export interface CompiledVehicleTimeline {
  vehicleId: string
  authoritativeSamples: TimedVehicleSample[]
  occurrences: TimedVehicleSample[]
  state: VehicleSegmentState
  playbackElapsedSeconds: number | null
  lastDisplayElapsedSeconds: number | null
  isolatedSinceElapsedSeconds: number | null
  isolationAnchor: VehicleTwinSample | null
  lastRecoveryReason?: string
  recoveryPending: boolean
  consecutiveValidSegmentCount: number
  lastMappedSequence: number | null
}

export interface VehicleMotionBufferStats {
  bufferSeconds: number
  sourceRate: number
  queuedFrames: number
  renderElapsedSeconds: number | null
  sourceGapP95Ms: number
  sourceGapP99Ms: number
  underrunCount: number
  underrunActive: boolean
  incompatiblePathInterpolationCount: number
  incompatiblePathInterpolationBlockedCount: number
  targetBufferSeconds: number
  expectedPlaybackRate: number
  globalBufferDepthSeconds: number
  globalPlaybackRate: number
  globalUnderrunPauseSeconds: number
  authoritativeInterpolationCount: number
  visibleTeleportCount: number
  pathResetCount: number
  movingFreezeFrameCount: number
  batchArrivalCount: number
  vehicleScaleViolationCount: number
  normalTransitionEpochViolationCount: number
  laneChangeCorridorViolationCount: number
  intermediateOffRoadFrameCount: number
  compiledSegmentCount: number
  rejectedCompiledSegmentCount: number
  endpointValidationFailureCount: number
  compiledReadyElapsedSeconds: number | null
  bufferedLookaheadSegmentCount: number
  compiledSegmentCacheHitCount: number
  compiledSegmentCacheHitRate: number
  isolatedVehicleCount: number
  maximumIsolationSeconds: number
  recoveredVehicleCount: number
  ghostVehicleIds: string[]
  hiddenUnresolvedVehicleIds: string[]
}

export interface TimedVehicleSample {
  frame: VehicleMotionSourceFrame
  sample: VehicleTwinSample
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function percentile(values: number[], ratio: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]
}

function shortestAngleDelta(from: number, to: number): number {
  const fullTurn = Math.PI * 2
  return ((to - from + Math.PI) % fullTurn + fullTurn) % fullTurn - Math.PI
}

function samplesCanShareMotionCurve(left: VehicleTwinSample, right: VehicleTwinSample): boolean {
  if (!left.motionPathKey || !right.motionPathKey) return true
  if (left.motionPathKey === right.motionPathKey) return true
  const corridorKeys = right.corridorMotionPathKeys ?? []
  if (
    right.roadTransitionKind === 'lane_change'
    && right.laneChangeCorridorKey
    && corridorKeys.includes(left.motionPathKey)
    && corridorKeys.includes(right.motionPathKey)
  ) return true
  if (
    right.motionPathBridgeKey
    && corridorKeys.includes(left.motionPathKey)
    && corridorKeys.includes(right.motionPathKey)
  ) return true
  if (
    right.roadTransitionKind === 'raw_continuous'
    && right.rawTransitionValidated === true
    && left.detailedCorridorValidation !== true
    && right.detailedCorridorValidation !== true
  ) return true
  // A label alone is not proof that two paths share physical geometry. The
  // locked SUMO connection is the explicit bridge across lane/path keys.
  return Boolean(
    left.connectionKey
    && right.connectionKey
    && left.connectionKey === right.connectionKey
    && right.roadTransitionKind === 'topology_successor',
  )
}

function applyLateralOffset(
  point: readonly [number, number, number],
  heading: number,
  lateralOffsetMeters: number,
): [number, number, number] {
  if (Math.abs(lateralOffsetMeters) <= 1e-9) return [...point]
  const latitudeRadians = point[1] * Math.PI / 180
  return [
    point[0] - Math.sin(heading) * lateralOffsetMeters
      / Math.max(1, Math.cos(latitudeRadians) * 110_900),
    point[1] + Math.cos(heading) * lateralOffsetMeters / 110_900,
    point[2],
  ]
}

function smoothLaneChangeProgress(amount: number): number {
  const value = clamp(amount, 0, 1)
  return value * value * (3 - 2 * value)
}

function signedLateralOffsetMeters(
  origin: readonly [number, number],
  point: readonly [number, number],
  heading: number,
): number {
  const latitudeRadians = origin[1] * Math.PI / 180
  const eastMeters = (point[0] - origin[0]) * Math.max(1, Math.cos(latitudeRadians) * 110_900)
  const northMeters = (point[1] - origin[1]) * 110_900
  return eastMeters * -Math.sin(heading) + northMeters * Math.cos(heading)
}

interface LaneChangeGuideSample {
  point: [number, number, number]
  heading: number
  pathArcDistanceMeters: number
  lateralOffsetMeters: number
}

function laneChangeGuidePoint(
  left: VehicleTwinSample,
  right: VehicleTwinSample,
  amount: number,
  sampler: MotionPathSampler | null,
): LaneChangeGuideSample | null {
  if (!sampler || !left.motionPathKey || !right.motionPathKey) return null
  const sourceStart = Number.isFinite(left.pathArcDistanceMeters)
    ? Number(left.pathArcDistanceMeters)
    : sampler.project(left.motionPathKey, [left.point[0], left.point[1]])?.pathArcDistanceMeters
  const targetEnd = Number.isFinite(right.pathArcDistanceMeters)
    ? Number(right.pathArcDistanceMeters)
    : sampler.project(right.motionPathKey, [right.point[0], right.point[1]])?.pathArcDistanceMeters
  if (sourceStart == null || targetEnd == null) return null
  const sourceProgress = clamp(amount, 0, 1)
  const durationSeconds = Math.max(0.001, (right.time - left.time) / 1_000)
  const sourceArcStart = Number(left.sourceArcDistanceMeters)
  const sourceArcEnd = Number(right.sourceArcDistanceMeters)
  const measuredAdvance = Number.isFinite(sourceArcStart)
    && Number.isFinite(sourceArcEnd)
    && sourceArcEnd >= sourceArcStart
    ? sourceArcEnd - sourceArcStart
    : Number.NaN
  const longitudinalAdvance = Number.isFinite(measuredAdvance)
    ? measuredAdvance
    : Math.max(
        0,
        (Math.max(0, Number(left.sourceSpeedMetersPerSecond) || 0)
          + Math.max(0, Number(right.sourceSpeedMetersPerSecond) || 0))
          * 0.5 * durationSeconds,
      )
  const sourceDistance = sourceStart + longitudinalAdvance * sourceProgress
  const targetStart = Math.max(0, targetEnd - longitudinalAdvance)
  const targetDistance = targetStart + (targetEnd - targetStart) * sourceProgress
  const sourceSample = sampler.sample(left.motionPathKey, sourceDistance)
  const targetSample = sampler.sample(right.motionPathKey, targetDistance)
  const sourceAnchor = sampler.sample(left.motionPathKey, sourceStart)
  const targetAnchor = sampler.sample(right.motionPathKey, targetEnd)
  if (!sourceSample || !targetSample || !sourceAnchor || !targetAnchor) return null
  const lateralProgress = smoothLaneChangeProgress(sourceProgress)
  const startLateralOffset = signedLateralOffsetMeters(
    [sourceAnchor.longitude, sourceAnchor.latitude],
    [left.point[0], left.point[1]],
    sourceAnchor.heading,
  )
  const endLateralOffset = signedLateralOffsetMeters(
    [targetAnchor.longitude, targetAnchor.latitude],
    [right.point[0], right.point[1]],
    targetAnchor.heading,
  )
  const lateralOffsetMeters = startLateralOffset
    + (endLateralOffset - startLateralOffset) * lateralProgress
  const sourcePoint = applyLateralOffset(
    [
      sourceSample.longitude,
      sourceSample.latitude,
      left.point[2] + (right.point[2] - left.point[2]) * sourceProgress,
    ],
    sourceSample.heading,
    startLateralOffset + (endLateralOffset - startLateralOffset) * sourceProgress,
  )
  const targetPoint = applyLateralOffset(
    [targetSample.longitude, targetSample.latitude, sourcePoint[2]],
    targetSample.heading,
    startLateralOffset + (endLateralOffset - startLateralOffset) * sourceProgress,
  )
  const point: [number, number, number] = [
    sourcePoint[0] + (targetPoint[0] - sourcePoint[0]) * lateralProgress,
    sourcePoint[1] + (targetPoint[1] - sourcePoint[1]) * lateralProgress,
    sourcePoint[2],
  ]
  return {
    point,
    heading: sourceSample.heading
      + shortestAngleDelta(sourceSample.heading, targetSample.heading) * lateralProgress,
    pathArcDistanceMeters: sourceDistance,
    lateralOffsetMeters,
  }
}

export interface MonotonePathDistanceSample {
  distanceMeters: number
  speedMetersPerSecond: number
}

export function interpolateMonotonePathDistance(
  startDistanceMeters: number,
  endDistanceMeters: number,
  startSpeedMetersPerSecond: number,
  endSpeedMetersPerSecond: number,
  durationSeconds: number,
  ratio: number,
  allowedSpeedMetersPerSecond = Number.POSITIVE_INFINITY,
  maximumSyntheticAccelerationMetersPerSecondSquared = MAX_SYNTHETIC_ACCELERATION_MPS2,
): MonotonePathDistanceSample {
  const amount = clamp(ratio, 0, 1)
  const distanceDelta = Math.max(0, endDistanceMeters - startDistanceMeters)
  const duration = Math.max(1e-3, durationSeconds)
  if (distanceDelta <= 1e-6) {
    return { distanceMeters: startDistanceMeters, speedMetersPerSecond: 0 }
  }
  const secantSpeed = distanceDelta / duration
  const unconstrainedMaximumSpeed = Math.max(
    secantSpeed,
    Math.max(0, startSpeedMetersPerSecond),
    Math.max(0, endSpeedMetersPerSecond),
  ) * MAX_INTERPOLATED_SPEED_RATIO
  const maximumSpeed = Number.isFinite(allowedSpeedMetersPerSecond)
    ? Math.max(secantSpeed, Math.min(
        unconstrainedMaximumSpeed,
        Math.max(0, allowedSpeedMetersPerSecond) * MAX_INTERPOLATED_SPEED_RATIO,
      ))
    : unconstrainedMaximumSpeed
  let startTangent = Math.min(Math.max(0, startSpeedMetersPerSecond), 3 * secantSpeed, maximumSpeed)
  let endTangent = Math.min(Math.max(0, endSpeedMetersPerSecond), 3 * secantSpeed, maximumSpeed)
  const magnitude = Math.hypot(startTangent / secantSpeed, endTangent / secantSpeed)
  if (magnitude > 3) {
    const scale = 3 / magnitude
    startTangent *= scale
    endTangent *= scale
  }
  const speedAt = (t: number) => {
    const t2 = t * t
    return (
      (6 * t2 - 6 * t) * startDistanceMeters / duration
      + (3 * t2 - 4 * t + 1) * startTangent
      + (-6 * t2 + 6 * t) * endDistanceMeters / duration
      + (3 * t2 - 2 * t) * endTangent
    )
  }
  const accelerationAt = (t: number) => (
    (12 * t - 6) * (startDistanceMeters - endDistanceMeters) / (duration * duration)
    + (6 * t - 4) * startTangent / duration
    + (6 * t - 2) * endTangent / duration
  )
  const backendAcceleration = Math.abs(endTangent - startTangent) / duration
  const maximumAcceleration = Math.max(
    maximumSyntheticAccelerationMetersPerSecondSquared,
    backendAcceleration,
  )
  for (let iteration = 0; iteration < 8; iteration += 1) {
    const peakSpeed = Math.max(...Array.from({ length: 17 }, (_, index) => speedAt(index / 16)))
    const peakAcceleration = Math.max(
      Math.abs(accelerationAt(0)),
      Math.abs(accelerationAt(1)),
    )
    if (peakSpeed <= maximumSpeed + 1e-6 && peakAcceleration <= maximumAcceleration + 1e-6) break
    startTangent = (startTangent + secantSpeed) / 2
    endTangent = (endTangent + secantSpeed) / 2
  }
  const t = amount
  const t2 = t * t
  const t3 = t2 * t
  const distanceMeters = clamp(
    (2 * t3 - 3 * t2 + 1) * startDistanceMeters
      + (t3 - 2 * t2 + t) * duration * startTangent
      + (-2 * t3 + 3 * t2) * endDistanceMeters
      + (t3 - t2) * duration * endTangent,
    startDistanceMeters,
    endDistanceMeters,
  )
  return {
    distanceMeters,
    speedMetersPerSecond: Math.max(0, Math.min(maximumSpeed, speedAt(t))),
  }
}

export function interpolateVehicleTwinSample(
  left: VehicleTwinSample,
  right: VehicleTwinSample,
  ratio: number,
  motionPathSampler: MotionPathSampler | null = null,
): VehicleTwinSample {
  const amount = clamp(ratio, 0, 1)
  const leftAxis = Number.isFinite(left.modelForwardAxisAngle) ? left.modelForwardAxisAngle : 0
  const rightAxis = Number.isFinite(right.modelForwardAxisAngle) ? right.modelForwardAxisAngle : 0
  const leftHeading = Number.isFinite(left.vehicleHeading) ? left.vehicleHeading : left.dir + leftAxis
  const rightHeading = Number.isFinite(right.vehicleHeading) ? right.vehicleHeading : right.dir + rightAxis
  const laneChangeTransition = right.transitionKind === 'lane_change'
    || right.roadTransitionKind === 'lane_change'
  let pathKey: string | null = null
  let startPathDistance = Number(left.pathArcDistanceMeters)
  let endPathDistance = Number(right.pathArcDistanceMeters)
  if (!laneChangeTransition && left.motionPathKey && left.motionPathKey === right.motionPathKey) {
    pathKey = left.motionPathKey
  } else if (
    !laneChangeTransition
    && right.motionPathKey
    && (right.transitionKind === 'topological' || Boolean(right.motionPathBridgeKey))
    && motionPathSampler
  ) {
    const startProjection = motionPathSampler.project(right.motionPathKey, [left.point[0], left.point[1]])
    const endProjection = motionPathSampler.project(right.motionPathKey, [right.point[0], right.point[1]])
    if (
      startProjection
      && endProjection
      && endProjection.pathArcDistanceMeters >= startProjection.pathArcDistanceMeters
    ) {
      pathKey = right.motionPathKey
      startPathDistance = startProjection.pathArcDistanceMeters
      endPathDistance = endProjection.pathArcDistanceMeters
    }
  }
  const durationSeconds = Math.max(0.001, (right.time - left.time) / 1_000)
  const pathDistance = pathKey
    && motionPathSampler
    && Number.isFinite(startPathDistance)
    && Number.isFinite(endPathDistance)
    && endPathDistance >= startPathDistance
    ? interpolateMonotonePathDistance(
        startPathDistance,
        endPathDistance,
        Number(left.sourceSpeedMetersPerSecond) || 0,
        Number(right.sourceSpeedMetersPerSecond) || 0,
        durationSeconds,
        amount,
        Math.max(
          Number(left.sourceAllowedSpeedMetersPerSecond) || 0,
          Number(right.sourceAllowedSpeedMetersPerSecond) || 0,
        ),
        Math.max(
          Number(left.maximumAccelerationMetersPerSecondSquared) || 0,
          Number(right.maximumAccelerationMetersPerSecondSquared) || 0,
        ),
      )
    : null
  const pathSample = pathKey && pathDistance
    ? motionPathSampler?.sample(pathKey, pathDistance.distanceMeters) ?? null
    : null
  const lateralOffsetMeters = Number.isFinite(left.sourceLateralOffsetMeters)
    && Number.isFinite(right.sourceLateralOffsetMeters)
    ? Number(left.sourceLateralOffsetMeters)
      + (Number(right.sourceLateralOffsetMeters) - Number(left.sourceLateralOffsetMeters)) * amount
    : Number.isFinite(right.sourceLateralOffsetMeters)
      ? Number(right.sourceLateralOffsetMeters)
      : Number(left.sourceLateralOffsetMeters) || 0
  const constrainedLaneChangePoint = laneChangeTransition
    ? laneChangeGuidePoint(left, right, amount, motionPathSampler)
    : null
  const point: [number, number, number] = pathSample
    ? applyLateralOffset([
      pathSample.longitude,
      pathSample.latitude,
      left.point[2] + (right.point[2] - left.point[2]) * amount,
    ], pathSample.heading, lateralOffsetMeters)
    : constrainedLaneChangePoint?.point ?? [
        left.point[0] + (right.point[0] - left.point[0]) * amount,
        left.point[1] + (right.point[1] - left.point[1]) * amount,
        left.point[2] + (right.point[2] - left.point[2]) * amount,
      ]
  const interpolatedSourceHeading = leftHeading
    + shortestAngleDelta(leftHeading, rightHeading) * amount
  const trajectoryHeading = pathSample?.heading ?? constrainedLaneChangePoint?.heading
  const vehicleHeading = trajectoryHeading != null
    && Math.abs(shortestAngleDelta(interpolatedSourceHeading, trajectoryHeading))
      <= MAX_MOTION_PATH_HEADING_DELTA
    ? trajectoryHeading
    : interpolatedSourceHeading
  const modelForwardAxisAngle = amount < 0.5
    ? leftAxis
    : rightAxis
  const corridorMotionPathKeys = [...new Set([
    ...(left.corridorMotionPathKeys ?? []),
    ...(right.corridorMotionPathKeys ?? []),
    ...(laneChangeTransition ? [left.motionPathKey, right.motionPathKey] : [pathKey]),
  ].filter((key): key is string => Boolean(key)))]
  const requiresCorridorValidation = Boolean(
    left.detailedCorridorValidation || right.detailedCorridorValidation
  )
  const trajectoryResolved = Boolean(pathSample || constrainedLaneChangePoint)
    || !requiresCorridorValidation
  const intermediatePoseValid = trajectoryResolved && (
    !requiresCorridorValidation
    || Boolean(
      motionPathSampler?.containsVehicle?.(
        corridorMotionPathKeys,
        [point[0], point[1]],
        trajectoryHeading ?? interpolatedSourceHeading,
        Math.max(0, Number(right.vehicleLengthMeters ?? left.vehicleLengthMeters) || 0) / 2,
        Math.max(0, Number(right.vehicleWidthMeters ?? left.vehicleWidthMeters) || 0) / 2,
        right.connectionLockStage === 'internal' || right.connectionLockStage === 'exiting',
      ),
    ))
  return {
    ...(amount < 0.5 ? left : right),
    id: left.id,
    point,
    dir: vehicleHeading - modelForwardAxisAngle,
    vehicleHeading,
    modelForwardAxisAngle,
    scale: [...left.scale] as [number, number, number],
    arcDistanceMeters: Number.isFinite(left.arcDistanceMeters) && Number.isFinite(right.arcDistanceMeters)
      ? Number(left.arcDistanceMeters) + (Number(right.arcDistanceMeters) - Number(left.arcDistanceMeters)) * amount
      : amount < 0.5 ? left.arcDistanceMeters : right.arcDistanceMeters,
    pathArcDistanceMeters: pathDistance?.distanceMeters
      ?? constrainedLaneChangePoint?.pathArcDistanceMeters
      ?? (Number.isFinite(left.pathArcDistanceMeters) && Number.isFinite(right.pathArcDistanceMeters)
        ? Number(left.pathArcDistanceMeters)
          + (Number(right.pathArcDistanceMeters) - Number(left.pathArcDistanceMeters)) * amount
        : amount < 0.5 ? left.pathArcDistanceMeters : right.pathArcDistanceMeters),
    sourceSpeedMetersPerSecond: pathDistance?.speedMetersPerSecond
      ?? (Number(left.sourceSpeedMetersPerSecond) || 0)
        + ((Number(right.sourceSpeedMetersPerSecond) || 0)
          - (Number(left.sourceSpeedMetersPerSecond) || 0)) * amount,
    sourceLateralOffsetMeters: constrainedLaneChangePoint?.lateralOffsetMeters
      ?? lateralOffsetMeters,
    corridorMotionPathKeys,
    intermediatePoseValid,
    intermediateValidationReason: intermediatePoseValid
      ? undefined
      : laneChangeTransition && !constrainedLaneChangePoint
        ? 'lane_change_path_unresolved'
        : laneChangeTransition ? 'lane_change_corridor_violation' : 'intermediate_off_road',
    authoritativeSourceTimeSeconds:
      (Number(left.authoritativeSourceTimeSeconds) || left.time / 1_000)
      + (
        (Number(right.authoritativeSourceTimeSeconds) || right.time / 1_000)
        - (Number(left.authoritativeSourceTimeSeconds) || left.time / 1_000)
      ) * amount,
    time: 0,
  }
}

function compiledMotionSegmentKey(
  left: TimedVehicleSample,
  right: TimedVehicleSample,
): string {
  return [
    left.sample.id,
    left.frame.sceneGeneration,
    left.frame.sequence,
    right.frame.sequence,
    left.sample.motionPathKey ?? '',
    right.sample.motionPathKey ?? '',
    right.sample.motionPathBridgeKey ?? '',
    right.sample.laneChangeCorridorKey ?? '',
  ].join('|')
}

export function compileMotionSegment(
  left: TimedVehicleSample,
  right: TimedVehicleSample,
  motionPathSampler: MotionPathSampler | null,
): CompiledMotionSegment {
  const key = compiledMotionSegmentKey(left, right)
  const durationSeconds = right.frame.elapsedSeconds - left.frame.elapsedSeconds
  const reject = (rejectionReason: string, validationSampleCount = 0): CompiledMotionSegment => ({
    key,
    vehicleId: left.sample.id,
    startElapsedSeconds: left.frame.elapsedSeconds,
    endElapsedSeconds: right.frame.elapsedSeconds,
    valid: false,
    validationSampleCount,
    routeSource: right.sample.dynamicConnectionEvidence?.source ?? 'unresolved',
    rejectionReason,
  })
  if (durationSeconds <= 1e-9) return reject('non_positive_authoritative_interval')
  if (
    left.sample.sceneGeneration !== right.sample.sceneGeneration
    || left.sample.motionEpoch !== right.sample.motionEpoch
  ) return reject('incompatible_motion_generation')
  if (!samplesCanShareMotionCurve(left.sample, right.sample)) {
    return reject('incompatible_motion_path')
  }
  const steps = Math.max(1, Math.ceil(durationSeconds * COMPILED_SEGMENT_VALIDATION_FPS))
  for (let index = 0; index <= steps; index += 1) {
    const sample = interpolateVehicleTwinSample(
      { ...left.sample, time: left.frame.elapsedSeconds * 1_000 },
      { ...right.sample, time: right.frame.elapsedSeconds * 1_000 },
      index / steps,
      motionPathSampler,
    )
    if (sample.intermediatePoseValid === false) {
      const location = index === 0 || index === steps ? 'endpoint' : 'intermediate'
      return reject(
        `${location}:${sample.intermediateValidationReason ?? 'off_road'}`,
        index + 1,
      )
    }
    if (sample.scale.some((value, axis) => Math.abs(value - left.sample.scale[axis]) > 1e-9)) {
      return reject('vehicle_scale_changed', index + 1)
    }
  }
  return {
    key,
    vehicleId: left.sample.id,
    startElapsedSeconds: left.frame.elapsedSeconds,
    endElapsedSeconds: right.frame.elapsedSeconds,
    valid: true,
    validationSampleCount: steps + 1,
    routeSource: right.sample.dynamicConnectionEvidence?.source === 'live_via'
      && (left.sample.dynamicConnectionEvidence?.source ?? 'unresolved') === 'unresolved'
      ? 'buffered_lookahead'
      : right.sample.dynamicConnectionEvidence?.source ?? 'unresolved',
  }
}

export class VehicleMotionBuffer {
  private frames: VehicleMotionSourceFrame[] = []
  private arrivalIntervalsMs: number[] = []
  private measuredRates: number[] = []
  private renderElapsedSeconds: number | null = null
  private lastOutputWallTimeMs: number | null = null
  private sourceRate = 1
  private bufferSeconds = MIN_VEHICLE_BUFFER_SECONDS
  private targetBufferSeconds = MIN_VEHICLE_BUFFER_SECONDS
  private expectedPlaybackRate = 1
  private underrunCount = 0
  private underrunActive = false
  private incompatiblePathInterpolationBlockedCount = 0
  private globalBufferDepthSeconds = 0
  private globalUnderrunPauseSeconds = 0
  private authoritativeInterpolationCount = 0
  private visibleTeleportCount = 0
  private pathResetCount = 0
  private movingFreezeFrameCount = 0
  private batchArrivalCount = 0
  private vehicleScaleViolationCount = 0
  private normalTransitionEpochViolationCount = 0
  private laneChangeCorridorViolationCount = 0
  private intermediateOffRoadFrameCount = 0
  private compiledSegmentCount = 0
  private rejectedCompiledSegmentCount = 0
  private endpointValidationFailureCount = 0
  private compiledReadyElapsedSeconds: number | null = null
  private bufferedLookaheadSegmentCount = 0
  private compiledSegmentCacheHitCount = 0
  private isolatedVehicleCount = 0
  private maximumIsolationSeconds = 0
  private recoveredVehicleCount = 0
  private ghostVehicleIds: string[] = []
  private hiddenUnresolvedVehicleIds: string[] = []
  private readonly compiledSegments = new Map<string, CompiledMotionSegment>()
  private readonly timelines = new Map<string, CompiledVehicleTimeline>()
  private sceneGeneration: number | null = null
  private motionPathSampler: MotionPathSampler | null = null
  private lastLegalOutputByVehicleId = new Map<string, VehicleTwinSample>()

  setMotionPathSampler(value: MotionPathSampler | null): void {
    this.motionPathSampler = value
  }

  push(frame: VehicleMotionSourceFrame): boolean {
    if (
      !Number.isFinite(frame.sceneGeneration)
      || !Number.isFinite(frame.sequence)
      || !Number.isFinite(frame.elapsedSeconds)
      || !Number.isFinite(frame.arrivalTimeMs)
    ) return false

    if (this.sceneGeneration != null && frame.sceneGeneration < this.sceneGeneration) return false
    if (this.sceneGeneration !== frame.sceneGeneration) {
      this.reset()
      this.sceneGeneration = frame.sceneGeneration
    }

    const deduplicatedSamples = [...new Map(
      frame.samples.map((sample) => [sample.id, sample] as const),
    ).values()]
    const normalizedFrame = {
      ...frame,
      presentVehicleIds: [...new Set(
        frame.presentVehicleIds ?? deduplicatedSamples.map((sample) => sample.id),
      )],
      samples: deduplicatedSamples.map((sample) => ({
        ...sample,
        point: [sample.point[0], sample.point[1], sample.point[2]] as [number, number, number],
        sampleQuality: sample.sampleQuality
          ?? (sample.poseSource === 'held' ? 'held' : 'authoritative'),
        authoritativeArcDistanceMeters: (sample.sampleQuality ?? 'authoritative') === 'authoritative'
          && Number.isFinite(sample.arcDistanceMeters)
          ? Number(sample.arcDistanceMeters)
          : sample.authoritativeArcDistanceMeters,
        authoritativePathArcDistanceMeters:
          (sample.sampleQuality ?? 'authoritative') === 'authoritative'
          && Number.isFinite(sample.pathArcDistanceMeters)
            ? Number(sample.pathArcDistanceMeters)
            : sample.authoritativePathArcDistanceMeters,
      })),
    }
    const existingIndex = this.frames.findIndex((entry) => entry.sequence === frame.sequence)
    if (existingIndex >= 0) {
      this.frames[existingIndex] = normalizedFrame
      return false
    }

    const previous = this.frames.at(-1)
    if (
      previous
      && (frame.sequence < previous.sequence || frame.elapsedSeconds <= previous.elapsedSeconds)
    ) return false

    if (previous) this.updateTiming(previous, normalizedFrame)
    this.frames.push(normalizedFrame)
    this.updateCompiledTimelines(normalizedFrame)
    if (this.frames.length > MAX_SOURCE_FRAMES) {
      this.frames.splice(0, this.frames.length - MAX_SOURCE_FRAMES)
    }
    return true
  }

  sample(
    wallTimeMs: number,
    requestedDisplayElapsedSeconds: number | null = null,
  ): VehicleTwinSample[] | null {
    if (this.frames.length < 2 || !Number.isFinite(wallTimeMs)) return null
    const first = this.frames[0]
    const latest = this.frames.at(-1)!
    const readyElapsedSeconds = Math.min(
      latest.elapsedSeconds,
      this.compiledReadyElapsedSeconds ?? this.frames[0].elapsedSeconds,
    )
    if (requestedDisplayElapsedSeconds != null) {
      if (
        !Number.isFinite(requestedDisplayElapsedSeconds)
        || requestedDisplayElapsedSeconds < first.elapsedSeconds - 1e-6
        || requestedDisplayElapsedSeconds > latest.elapsedSeconds + 1e-6
      ) return null
      this.renderElapsedSeconds = Math.max(
        this.renderElapsedSeconds ?? requestedDisplayElapsedSeconds,
        requestedDisplayElapsedSeconds,
      )
      this.lastOutputWallTimeMs = wallTimeMs
      this.globalBufferDepthSeconds = Math.max(0, latest.elapsedSeconds - this.renderElapsedSeconds)
      this.expectedPlaybackRate = 1
      const result = this.interpolateAt(this.renderElapsedSeconds)
      this.pruneConsumedFrames()
      return result
    }
    const startupBufferSeconds = this.bufferSeconds
    if (
      this.renderElapsedSeconds == null
      && (
        latest.elapsedSeconds - first.elapsedSeconds + 1e-9 < startupBufferSeconds
        || readyElapsedSeconds <= first.elapsedSeconds + 1e-9
      )
    ) return null

    if (this.renderElapsedSeconds == null) {
      this.renderElapsedSeconds = Math.max(
        first.elapsedSeconds,
        Math.min(latest.elapsedSeconds - this.bufferSeconds, readyElapsedSeconds),
      )
      this.lastOutputWallTimeMs = wallTimeMs
    } else {
      const wallDeltaSeconds = this.lastOutputWallTimeMs == null
        ? 0
        : clamp((wallTimeMs - this.lastOutputWallTimeMs) / 1_000, 0, MAX_OUTPUT_STEP_SECONDS)
      this.lastOutputWallTimeMs = wallTimeMs
      const targetElapsedSeconds = Math.min(
        latest.elapsedSeconds - this.bufferSeconds,
        readyElapsedSeconds,
      )
      const bufferDepthSeconds = latest.elapsedSeconds - this.renderElapsedSeconds
      this.globalBufferDepthSeconds = Math.max(0, bufferDepthSeconds)
      const recoveryRatio = targetElapsedSeconds > this.renderElapsedSeconds
        ? Math.min(MAX_PLAYBACK_RATE_RATIO, 1 + (targetElapsedSeconds - this.renderElapsedSeconds) * 0.025)
        : 1
      const underrunRatio = bufferDepthSeconds <= 0.05
        ? 0
        : bufferDepthSeconds >= this.bufferSeconds
        ? 1
        : clamp(bufferDepthSeconds / UNDERRUN_SLOWDOWN_WINDOW_SECONDS, 0, 1)
      this.expectedPlaybackRate = this.sourceRate * recoveryRatio * underrunRatio
      this.renderElapsedSeconds += wallDeltaSeconds * this.expectedPlaybackRate
      this.renderElapsedSeconds = Math.min(this.renderElapsedSeconds, readyElapsedSeconds)
      if (this.expectedPlaybackRate <= 1e-6) this.globalUnderrunPauseSeconds += wallDeltaSeconds
      const nextUnderrunActive = bufferDepthSeconds < this.bufferSeconds - 1e-6
      if (nextUnderrunActive && !this.underrunActive) this.underrunCount += 1
      this.underrunActive = nextUnderrunActive
    }

    const result = this.interpolateAt(this.renderElapsedSeconds)
    this.pruneConsumedFrames()
    return result
  }

  pause(): void {
    this.lastOutputWallTimeMs = null
  }

  reset(): void {
    this.frames = []
    this.arrivalIntervalsMs = []
    this.measuredRates = []
    this.renderElapsedSeconds = null
    this.lastOutputWallTimeMs = null
    this.sourceRate = 1
    this.bufferSeconds = MIN_VEHICLE_BUFFER_SECONDS
    this.targetBufferSeconds = MIN_VEHICLE_BUFFER_SECONDS
    this.expectedPlaybackRate = 1
    this.underrunCount = 0
    this.underrunActive = false
    this.incompatiblePathInterpolationBlockedCount = 0
    this.globalBufferDepthSeconds = 0
    this.globalUnderrunPauseSeconds = 0
    this.authoritativeInterpolationCount = 0
    this.visibleTeleportCount = 0
    this.pathResetCount = 0
    this.movingFreezeFrameCount = 0
    this.batchArrivalCount = 0
    this.vehicleScaleViolationCount = 0
    this.normalTransitionEpochViolationCount = 0
    this.laneChangeCorridorViolationCount = 0
    this.intermediateOffRoadFrameCount = 0
    this.compiledSegmentCount = 0
    this.rejectedCompiledSegmentCount = 0
    this.endpointValidationFailureCount = 0
    this.compiledReadyElapsedSeconds = null
    this.bufferedLookaheadSegmentCount = 0
    this.compiledSegmentCacheHitCount = 0
    this.isolatedVehicleCount = 0
    this.maximumIsolationSeconds = 0
    this.recoveredVehicleCount = 0
    this.ghostVehicleIds = []
    this.hiddenUnresolvedVehicleIds = []
    this.compiledSegments.clear()
    this.timelines.clear()
    this.sceneGeneration = null
    this.lastLegalOutputByVehicleId.clear()
  }

  stats(): VehicleMotionBufferStats {
    return {
      bufferSeconds: this.bufferSeconds,
      sourceRate: this.sourceRate,
      queuedFrames: this.frames.length,
      renderElapsedSeconds: this.renderElapsedSeconds,
      sourceGapP95Ms: percentile(this.arrivalIntervalsMs, 0.95),
      sourceGapP99Ms: percentile(this.arrivalIntervalsMs, 0.99),
      underrunCount: this.underrunCount,
      underrunActive: this.underrunActive,
      incompatiblePathInterpolationCount: 0,
      incompatiblePathInterpolationBlockedCount: this.incompatiblePathInterpolationBlockedCount,
      targetBufferSeconds: this.targetBufferSeconds,
      expectedPlaybackRate: this.expectedPlaybackRate,
      globalBufferDepthSeconds: this.globalBufferDepthSeconds,
      globalPlaybackRate: this.expectedPlaybackRate,
      globalUnderrunPauseSeconds: this.globalUnderrunPauseSeconds,
      authoritativeInterpolationCount: this.authoritativeInterpolationCount,
      visibleTeleportCount: this.visibleTeleportCount,
      pathResetCount: this.pathResetCount,
      movingFreezeFrameCount: this.movingFreezeFrameCount,
      batchArrivalCount: this.batchArrivalCount,
      vehicleScaleViolationCount: this.vehicleScaleViolationCount,
      normalTransitionEpochViolationCount: this.normalTransitionEpochViolationCount,
      laneChangeCorridorViolationCount: this.laneChangeCorridorViolationCount,
      intermediateOffRoadFrameCount: this.intermediateOffRoadFrameCount,
      compiledSegmentCount: this.compiledSegmentCount,
      rejectedCompiledSegmentCount: this.rejectedCompiledSegmentCount,
      endpointValidationFailureCount: this.endpointValidationFailureCount,
      compiledReadyElapsedSeconds: this.compiledReadyElapsedSeconds,
      bufferedLookaheadSegmentCount: this.bufferedLookaheadSegmentCount,
      compiledSegmentCacheHitCount: this.compiledSegmentCacheHitCount,
      compiledSegmentCacheHitRate: this.compiledSegmentCount + this.compiledSegmentCacheHitCount > 0
        ? this.compiledSegmentCacheHitCount
          / (this.compiledSegmentCount + this.compiledSegmentCacheHitCount)
        : 0,
      isolatedVehicleCount: this.isolatedVehicleCount,
      maximumIsolationSeconds: this.maximumIsolationSeconds,
      recoveredVehicleCount: this.recoveredVehicleCount,
      ghostVehicleIds: [...this.ghostVehicleIds],
      hiddenUnresolvedVehicleIds: [...this.hiddenUnresolvedVehicleIds],
    }
  }

  private updateCompiledTimelines(current: VehicleMotionSourceFrame): void {
    const samplesById = new Map(current.samples.map((sample) => [sample.id, sample] as const))
    const presentVehicleIds = current.presentVehicleIds
      ?? current.samples.map((sample) => sample.id)
    for (const vehicleId of presentVehicleIds) {
      if (samplesById.has(vehicleId)) continue
      const timeline = this.timelines.get(vehicleId) ?? this.createTimeline(vehicleId)
      timeline.recoveryPending = true
      timeline.consecutiveValidSegmentCount = 0
      this.timelines.set(vehicleId, timeline)
    }
    for (const sample of current.samples) {
      const timeline = this.timelines.get(sample.id) ?? this.createTimeline(sample.id)
      const right = { frame: current, sample }
      timeline.occurrences.push(right)
      this.timelines.set(sample.id, timeline)
      const quality = sample.sampleQuality ?? 'authoritative'
      if (quality !== 'authoritative') continue
      const previous = timeline.authoritativeSamples.at(-1) ?? null
      if (!previous) {
        timeline.authoritativeSamples.push(right)
        timeline.lastMappedSequence = current.sequence
        continue
      }
      const key = compiledMotionSegmentKey(previous, right)
      let segment = this.compiledSegments.get(key)
      if (!segment) {
        segment = compileMotionSegment(previous, right, this.motionPathSampler)
        this.compiledSegments.set(key, segment)
        this.compiledSegmentCount += 1
        if (!segment.valid) {
          this.rejectedCompiledSegmentCount += 1
          if (segment.rejectionReason === 'incompatible_motion_path') {
            this.incompatiblePathInterpolationBlockedCount += 1
          }
          if (segment.rejectionReason?.includes('lane_change')) {
            this.laneChangeCorridorViolationCount += 1
          }
          if (
            segment.rejectionReason?.includes('off_road')
            || segment.rejectionReason?.includes('corridor_violation')
          ) this.intermediateOffRoadFrameCount += 1
          if (segment.rejectionReason?.startsWith('endpoint:')) {
            this.endpointValidationFailureCount += 1
          }
        }
        if (segment.valid && segment.routeSource === 'buffered_lookahead') {
          this.bufferedLookaheadSegmentCount += 1
        }
      }
      if (segment.valid) {
        timeline.consecutiveValidSegmentCount += 1
        const recoveryRequired = timeline.recoveryPending
        const playable = !recoveryRequired || timeline.consecutiveValidSegmentCount >= 2
        segment.recoveryRequired = recoveryRequired
        segment.playable = playable
        segment.consecutiveValidCount = timeline.consecutiveValidSegmentCount
        if (playable) timeline.recoveryPending = false
      } else {
        timeline.recoveryPending = true
        timeline.consecutiveValidSegmentCount = 0
      }
      // Every authoritative endpoint is retained. Segment validity controls
      // playback, never whether the next interval can be compiled.
      timeline.authoritativeSamples.push(right)
      timeline.lastMappedSequence = current.sequence
    }
    // Global playback readiness follows source depth only. An invalid vehicle
    // segment is isolated on its own timeline and must not pause the scene.
    this.compiledReadyElapsedSeconds = current.elapsedSeconds
  }

  private createTimeline(vehicleId: string): CompiledVehicleTimeline {
    return {
      vehicleId,
      authoritativeSamples: [],
      occurrences: [],
      state: 'ready',
      playbackElapsedSeconds: null,
      lastDisplayElapsedSeconds: null,
      isolatedSinceElapsedSeconds: null,
      isolationAnchor: null,
      recoveryPending: false,
      consecutiveValidSegmentCount: 0,
      lastMappedSequence: null,
    }
  }

  private updateTiming(
    previous: VehicleMotionSourceFrame,
    current: VehicleMotionSourceFrame,
  ): void {
    const wallDeltaMs = current.arrivalTimeMs - previous.arrivalTimeMs
    const simulationDelta = current.elapsedSeconds - previous.elapsedSeconds
    if (wallDeltaMs <= 0 || wallDeltaMs > MAX_TRACKED_SOURCE_GAP_MS || simulationDelta <= 0) {
      return
    }
    if (wallDeltaMs < MIN_RATE_INTERVAL_MS) {
      this.batchArrivalCount += 1
      return
    }
    const measuredRate = clamp(simulationDelta / (wallDeltaMs / 1_000), 0.05, 20)
    this.measuredRates.push(measuredRate)
    this.measuredRates = this.measuredRates.slice(-ROBUST_RATE_SAMPLE_COUNT)
    const robustRate = percentile(this.measuredRates, 0.5)
    this.sourceRate += (robustRate - this.sourceRate) * 0.2
    this.arrivalIntervalsMs.push(wallDeltaMs)
    this.arrivalIntervalsMs = this.arrivalIntervalsMs.slice(-MAX_TIMING_SAMPLES)
    const p99IntervalSeconds = percentile(this.arrivalIntervalsMs, 0.99) / 1_000
    const requestedBuffer = clamp(
      p99IntervalSeconds * BUFFER_INTERVAL_MULTIPLIER * this.sourceRate,
      MIN_VEHICLE_BUFFER_SECONDS,
      MAX_VEHICLE_BUFFER_SECONDS,
    )
    this.targetBufferSeconds = requestedBuffer
    if (requestedBuffer > this.bufferSeconds) {
      this.bufferSeconds = requestedBuffer
    }
  }

  private interpolateAt(elapsedSeconds: number): VehicleTwinSample[] {
    const samples: VehicleTwinSample[] = []
    let isolatedVehicleCount = 0
    for (const [vehicleId, timeline] of this.timelines) {
      const resolved = this.resolveVehicleAt(timeline, elapsedSeconds)
      if (resolved) {
        samples.push(resolved)
        this.lastLegalOutputByVehicleId.set(vehicleId, this.cloneSample(
          resolved,
          resolved.sampleQuality,
          resolved.displayElapsedSeconds,
        ))
      } else {
        this.lastLegalOutputByVehicleId.delete(vehicleId)
      }
      if (timeline.state !== 'ready') isolatedVehicleCount += 1
    }
    this.isolatedVehicleCount = isolatedVehicleCount
    const authoritativeIds = this.vehicleRosterAt(elapsedSeconds)
    const outputIds = new Set(samples.map((sample) => sample.id))
    this.ghostVehicleIds = [...outputIds].filter((id) => !authoritativeIds.has(id))
    this.hiddenUnresolvedVehicleIds = [...authoritativeIds].filter((id) => !outputIds.has(id))
    return samples
  }

  private resolveVehicleAt(
    timeline: CompiledVehicleTimeline,
    elapsedSeconds: number,
  ): VehicleTwinSample | null {
    if (timeline.occurrences.length === 0 || !this.vehicleIsPresentAt(timeline.vehicleId, elapsedSeconds)) {
      return null
    }
    const previousDisplayElapsedSeconds = timeline.lastDisplayElapsedSeconds
    const displayDeltaSeconds = previousDisplayElapsedSeconds == null
      ? 0
      : Math.max(0, elapsedSeconds - previousDisplayElapsedSeconds)
    timeline.lastDisplayElapsedSeconds = elapsedSeconds
    if (timeline.playbackElapsedSeconds == null) timeline.playbackElapsedSeconds = elapsedSeconds

    const regular = this.resolveAuthoritativeTimelineAt(timeline, elapsedSeconds)
    if (regular) {
      if (timeline.state !== 'ready') this.recoveredVehicleCount += 1
      timeline.state = 'ready'
      timeline.isolatedSinceElapsedSeconds = null
      timeline.isolationAnchor = null
      timeline.lastRecoveryReason = undefined
      timeline.playbackElapsedSeconds = elapsedSeconds
      return this.stabilizeResolvedSample(timeline, regular, elapsedSeconds, displayDeltaSeconds)
    }

    if (timeline.state !== 'isolated') {
      timeline.state = 'isolated'
      timeline.isolatedSinceElapsedSeconds = elapsedSeconds
      timeline.isolationAnchor = null
      timeline.lastRecoveryReason = 'compiled_segment_unavailable'
    }
    const isolatedSince = timeline.isolatedSinceElapsedSeconds ?? elapsedSeconds
    this.maximumIsolationSeconds = Math.max(
      this.maximumIsolationSeconds,
      elapsedSeconds - isolatedSince,
    )
    return null
  }

  private vehicleIsPresentAt(vehicleId: string, elapsedSeconds: number): boolean {
    return this.vehicleRosterAt(elapsedSeconds).has(vehicleId)
  }

  private vehicleRosterAt(elapsedSeconds: number): Set<string> {
    let low = 0
    let high = this.frames.length
    while (low < high) {
      const middle = Math.floor((low + high) / 2)
      if (this.frames[middle].elapsedSeconds <= elapsedSeconds) low = middle + 1
      else high = middle
    }
    const frame = low > 0 ? this.frames[low - 1] : null
    if (!frame) return new Set()
    return new Set(frame.presentVehicleIds ?? frame.samples.map((sample) => sample.id))
  }

  private resolveAuthoritativeTimelineAt(
    timeline: CompiledVehicleTimeline,
    elapsedSeconds: number,
  ): VehicleTwinSample | null {
    const authoritative = timeline.authoritativeSamples
    if (authoritative.length === 0) return null
    let low = 0
    let high = authoritative.length
    while (low < high) {
      const middle = Math.floor((low + high) / 2)
      if (authoritative[middle].frame.elapsedSeconds <= elapsedSeconds) low = middle + 1
      else high = middle
    }
    const left = low > 0 ? authoritative[low - 1] : null
    const right = low < authoritative.length ? authoritative[low] : null
    if (left && right && left !== right) {
      const duration = right.frame.elapsedSeconds - left.frame.elapsedSeconds
      const ratio = duration > 1e-9
        ? clamp((elapsedSeconds - left.frame.elapsedSeconds) / duration, 0, 1)
        : 0
      if (
        left.sample.sceneGeneration !== right.sample.sceneGeneration
        || left.sample.motionEpoch !== right.sample.motionEpoch
      ) {
        if (right.sample.roadTransitionKind !== 'incompatible') {
          this.normalTransitionEpochViolationCount += 1
        }
        return null
      }
      const compiled = this.compiledSegments.get(compiledMotionSegmentKey(left, right))
      if (!compiled?.valid || compiled.playable === false) {
        return null
      }
      this.compiledSegmentCacheHitCount += 1
      const interpolated = interpolateVehicleTwinSample(
        { ...left.sample, time: left.frame.elapsedSeconds * 1_000 },
        { ...right.sample, time: right.frame.elapsedSeconds * 1_000 },
        ratio,
        this.motionPathSampler,
      )
      if (interpolated.intermediatePoseValid === false) {
        this.intermediateOffRoadFrameCount += 1
        if (interpolated.intermediateValidationReason === 'lane_change_corridor_violation') {
          this.laneChangeCorridorViolationCount += 1
        }
        return null
      }
      this.authoritativeInterpolationCount += 1
      return {
        ...interpolated,
        sampleQuality: 'authoritative',
        predictionElapsedSeconds: 0,
        displayElapsedSeconds: elapsedSeconds,
      }
    }
    if (left && Math.abs(left.frame.elapsedSeconds - elapsedSeconds) <= 1e-6) {
      return this.cloneSample(left.sample, 'authoritative', elapsedSeconds)
    }
    return null
  }

  private stabilizeResolvedSample(
    timeline: CompiledVehicleTimeline,
    candidate: VehicleTwinSample,
    elapsedSeconds: number,
    displayDeltaSeconds: number,
  ): VehicleTwinSample {
    const previous = this.lastLegalOutputByVehicleId.get(timeline.vehicleId)
    if (!previous || displayDeltaSeconds <= 1e-9) return candidate
    const previousArc = Number(previous.pathArcDistanceMeters)
    const candidateArc = Number(candidate.pathArcDistanceMeters)
    if (
      previous.motionPathKey
      && previous.motionPathKey === candidate.motionPathKey
      && Number.isFinite(previousArc)
      && Number.isFinite(candidateArc)
    ) {
      if (candidateArc + 0.02 < previousArc) {
        timeline.state = 'recovering'
        return this.cloneSample(previous, 'held', elapsedSeconds)
      }
      const speedLimit = Math.max(
        Number(previous.sourceSpeedMetersPerSecond) || 0,
        Number(candidate.sourceSpeedMetersPerSecond) || 0,
        Number(previous.sourceAllowedSpeedMetersPerSecond) || 0,
        Number(candidate.sourceAllowedSpeedMetersPerSecond) || 0,
      ) * MAX_INTERPOLATED_SPEED_RATIO
      const maximumStep = Math.max(
        0.02,
        speedLimit * displayDeltaSeconds
          + 0.5 * MAX_SYNTHETIC_ACCELERATION_MPS2 * displayDeltaSeconds * displayDeltaSeconds,
      )
      if (candidateArc - previousArc > maximumStep && this.motionPathSampler) {
        const limitedArc = previousArc + maximumStep
        const pathSample = this.motionPathSampler.sample(candidate.motionPathKey, limitedArc)
        if (pathSample) {
          timeline.state = 'recovering'
          return {
            ...candidate,
            point: [pathSample.longitude, pathSample.latitude, candidate.point[2]],
            dir: pathSample.heading - (Number(candidate.modelForwardAxisAngle) || 0),
            vehicleHeading: pathSample.heading,
            pathArcDistanceMeters: limitedArc,
            sampleQuality: 'held',
            displayElapsedSeconds: elapsedSeconds,
          }
        }
      }
    }
    return candidate
  }

  private cloneSample(
    sample: VehicleTwinSample,
    sampleQuality: VehicleTwinSample['sampleQuality'],
    displayElapsedSeconds = sample.displayElapsedSeconds,
  ): VehicleTwinSample {
    return {
      ...sample,
      point: [...sample.point] as [number, number, number],
      time: 0,
      sampleQuality,
      displayElapsedSeconds,
    }
  }

  private pruneConsumedFrames(): void {
    if (this.renderElapsedSeconds == null) return
    const cutoff = this.renderElapsedSeconds - MAX_VEHICLE_BUFFER_SECONDS - 3
    let pruned = false
    while (
      this.frames.length > 2
      && this.frames[1].elapsedSeconds <= cutoff
    ) {
      this.frames.shift()
      pruned = true
    }
    if (!pruned) return
    for (const [vehicleId, timeline] of this.timelines) {
      while (
        timeline.occurrences.length > 1
        && timeline.occurrences[1].frame.elapsedSeconds <= cutoff
      ) timeline.occurrences.shift()
      while (
        timeline.authoritativeSamples.length > 1
        && timeline.authoritativeSamples[1].frame.elapsedSeconds <= cutoff
      ) timeline.authoritativeSamples.shift()
      if (
        (timeline.occurrences.length === 0
          || timeline.occurrences.at(-1)!.frame.elapsedSeconds <= cutoff)
        && !this.lastLegalOutputByVehicleId.has(vehicleId)
      ) this.timelines.delete(vehicleId)
    }
    for (const [key, segment] of this.compiledSegments) {
      if (segment.endElapsedSeconds <= cutoff) this.compiledSegments.delete(key)
    }
  }
}
