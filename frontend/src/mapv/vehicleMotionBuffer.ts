import type { VehicleTwinSample } from './vehicleTwinSample'
import {
  MAX_MOTION_PATH_HEADING_DELTA,
  type MotionPathSampler,
} from './realistic/intersectionLaneHeading.ts'

export const MIN_VEHICLE_BUFFER_SECONDS = 1.5
export const MAX_VEHICLE_BUFFER_SECONDS = 3
const BUFFER_INTERVAL_MULTIPLIER = 2
const MAX_OUTPUT_STEP_SECONDS = 0.25
const MAX_TIMING_SAMPLES = 30
const ROBUST_RATE_SAMPLE_COUNT = 9
const MAX_SOURCE_FRAMES = 40
const MAX_TRACKED_SOURCE_GAP_MS = 10_000
const MIN_RATE_INTERVAL_MS = 100
const BUFFER_SHRINK_STABLE_MS = 10_000
const MIN_PLAYBACK_RATE_RATIO = 0.9
const MAX_PLAYBACK_RATE_RATIO = 1.1
const TOPOLOGY_PREDICTION_SECONDS = 1
const RAW_PREDICTION_SECONDS = 0.5
const MAX_PREDICTION_DECELERATION_MPS2 = 3
const MIN_PREDICTION_SPEED_MPS = 0.35
const MIN_RECONCILIATION_SECONDS = 0.8
const MAX_INTERPOLATED_SPEED_RATIO = 1.1
const MAX_SYNTHETIC_ACCELERATION_MPS2 = 3
export interface VehicleMotionSourceFrame {
  sceneGeneration: number
  sequence: number
  elapsedSeconds: number
  arrivalTimeMs: number
  samples: VehicleTwinSample[]
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
  targetBufferSeconds: number
  expectedPlaybackRate: number
  predictedVehicleCount: number
  maximumPredictionSeconds: number
  reconcilingVehicleCount: number
  maximumReconciliationSpeedMetersPerSecond: number
  maximumReconciliationAccelerationMetersPerSecondSquared: number
  movingFreezeFrameCount: number
  batchArrivalCount: number
  vehicleScaleViolationCount: number
  normalTransitionEpochViolationCount: number
  authoritativePositionOverrideCount: number
  backwardArcMovementCount: number
  predictionForwardClampCount: number
}

interface TimedVehicleSample {
  frame: VehicleMotionSourceFrame
  sample: VehicleTwinSample
}

interface VehicleReconciliation {
  startedAtElapsedSeconds: number
  durationSeconds: number
  pathOffsetMeters?: number
  longitudeOffset?: number
  latitudeOffset?: number
}

interface PresentedVehicleState {
  sample: VehicleTwinSample
  elapsedSeconds: number
  displaySpeedMetersPerSecond: number
  previousTargetSpeedMetersPerSecond: number
  reconciliation?: VehicleReconciliation
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
  return right.roadTransitionKind === 'same_path'
    || right.roadTransitionKind === 'topology_successor'
    || right.roadTransitionKind === 'lane_change'
    || right.roadTransitionKind === 'raw_continuous'
    || right.transitionKind === 'topological'
    || right.transitionKind === 'lane_change'
}

interface HermitePlanarPoint {
  x: number
  y: number
}

function hermitePlanarPoint(
  east: number,
  north: number,
  parameter: number,
  leftHeading: number,
  rightHeading: number,
): HermitePlanarPoint {
  const chord = Math.hypot(east, north)
  if (chord < 0.05) return { x: east * parameter, y: north * parameter }
  const t = parameter
  const t2 = t * t
  const t3 = t2 * t
  const h10 = t3 - 2 * t2 + t
  const h01 = -2 * t3 + 3 * t2
  const h11 = t3 - t2
  const tangentScale = Math.min(chord * 1.15, chord + 3)
  return {
    x: h10 * Math.cos(leftHeading) * tangentScale
      + h01 * east
      + h11 * Math.cos(rightHeading) * tangentScale,
    y: h10 * Math.sin(leftHeading) * tangentScale
      + h01 * north
      + h11 * Math.sin(rightHeading) * tangentScale,
  }
}

function arcLengthParameter(
  east: number,
  north: number,
  amount: number,
  leftHeading: number,
  rightHeading: number,
): number {
  const steps = 16
  const cumulative = [0]
  let previous = hermitePlanarPoint(east, north, 0, leftHeading, rightHeading)
  for (let index = 1; index <= steps; index += 1) {
    const point = hermitePlanarPoint(east, north, index / steps, leftHeading, rightHeading)
    cumulative.push(cumulative.at(-1)! + Math.hypot(point.x - previous.x, point.y - previous.y))
    previous = point
  }
  const total = cumulative.at(-1) ?? 0
  if (total <= 1e-6) return amount
  const target = clamp(amount, 0, 1) * total
  const foundIndex = cumulative.findIndex((distance) => distance >= target)
  const rightIndex = foundIndex < 1 ? 1 : foundIndex
  const leftDistance = cumulative[rightIndex - 1]
  const rightDistance = cumulative[rightIndex]
  const segmentRatio = rightDistance > leftDistance
    ? (target - leftDistance) / (rightDistance - leftDistance)
    : 0
  return ((rightIndex - 1) + segmentRatio) / steps
}

function hermiteGeographicPoint(
  left: VehicleTwinSample,
  right: VehicleTwinSample,
  amount: number,
  leftHeading: number,
  rightHeading: number,
): [number, number, number] {
  const latitude = (left.point[1] + right.point[1]) / 2 * Math.PI / 180
  const metersPerLongitude = Math.max(1, Math.cos(latitude) * 110_900)
  const east = (right.point[0] - left.point[0]) * metersPerLongitude
  const north = (right.point[1] - left.point[1]) * 110_900
  const parameter = arcLengthParameter(east, north, amount, leftHeading, rightHeading)
  const planar = hermitePlanarPoint(east, north, parameter, leftHeading, rightHeading)
  return [
    left.point[0] + planar.x / metersPerLongitude,
    left.point[1] + planar.y / 110_900,
    left.point[2] + (right.point[2] - left.point[2]) * amount,
  ]
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
): MonotonePathDistanceSample {
  const amount = clamp(ratio, 0, 1)
  const distanceDelta = Math.max(0, endDistanceMeters - startDistanceMeters)
  const duration = Math.max(1e-3, durationSeconds)
  if (distanceDelta <= 1e-6) {
    return { distanceMeters: startDistanceMeters, speedMetersPerSecond: 0 }
  }
  const secantSpeed = distanceDelta / duration
  const maximumSpeed = Math.max(
    secantSpeed,
    Math.max(0, startSpeedMetersPerSecond),
    Math.max(0, endSpeedMetersPerSecond),
  ) * MAX_INTERPOLATED_SPEED_RATIO
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
  const maximumAcceleration = Math.max(MAX_SYNTHETIC_ACCELERATION_MPS2, backendAcceleration)
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
  const geographicTransition = right.transitionKind === 'lane_change'
    || right.roadTransitionKind === 'lane_change'
    || right.roadTransitionKind === 'raw_continuous'
  let pathKey: string | null = null
  let startPathDistance = Number(left.pathArcDistanceMeters)
  let endPathDistance = Number(right.pathArcDistanceMeters)
  if (!geographicTransition && left.motionPathKey && left.motionPathKey === right.motionPathKey) {
    pathKey = left.motionPathKey
  } else if (
    !geographicTransition
    && right.motionPathKey
    && right.transitionKind === 'topological'
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
  const point: [number, number, number] = pathSample
    ? applyLateralOffset([
      pathSample.longitude,
      pathSample.latitude,
      left.point[2] + (right.point[2] - left.point[2]) * amount,
    ], pathSample.heading, lateralOffsetMeters)
    : geographicTransition
      ? hermiteGeographicPoint(left, right, amount, leftHeading, rightHeading)
      : [
          left.point[0] + (right.point[0] - left.point[0]) * amount,
          left.point[1] + (right.point[1] - left.point[1]) * amount,
          left.point[2] + (right.point[2] - left.point[2]) * amount,
        ]
  const interpolatedSourceHeading = leftHeading
    + shortestAngleDelta(leftHeading, rightHeading) * amount
  const vehicleHeading = pathSample
    && Math.abs(shortestAngleDelta(interpolatedSourceHeading, pathSample.heading))
      <= MAX_MOTION_PATH_HEADING_DELTA
    ? pathSample.heading
    : interpolatedSourceHeading
  const modelForwardAxisAngle = amount < 0.5
    ? leftAxis
    : rightAxis
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
      ?? (Number.isFinite(left.pathArcDistanceMeters) && Number.isFinite(right.pathArcDistanceMeters)
        ? Number(left.pathArcDistanceMeters)
          + (Number(right.pathArcDistanceMeters) - Number(left.pathArcDistanceMeters)) * amount
        : amount < 0.5 ? left.pathArcDistanceMeters : right.pathArcDistanceMeters),
    sourceSpeedMetersPerSecond: pathDistance?.speedMetersPerSecond
      ?? (Number(left.sourceSpeedMetersPerSecond) || 0)
        + ((Number(right.sourceSpeedMetersPerSecond) || 0)
          - (Number(left.sourceSpeedMetersPerSecond) || 0)) * amount,
    sourceLateralOffsetMeters: lateralOffsetMeters,
    time: 0,
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
  private bufferStableSinceMs: number | null = null
  private underrunCount = 0
  private underrunActive = false
  private incompatiblePathInterpolationCount = 0
  private predictedVehicleCount = 0
  private maximumPredictionSeconds = 0
  private reconcilingVehicleCount = 0
  private maximumReconciliationSpeedMetersPerSecond = 0
  private maximumReconciliationAccelerationMetersPerSecondSquared = 0
  private movingFreezeFrameCount = 0
  private batchArrivalCount = 0
  private vehicleScaleViolationCount = 0
  private normalTransitionEpochViolationCount = 0
  private authoritativePositionOverrideCount = 0
  private backwardArcMovementCount = 0
  private predictionForwardClampCount = 0
  private sceneGeneration: number | null = null
  private motionPathSampler: MotionPathSampler | null = null
  private presentedVehicles = new Map<string, PresentedVehicleState>()

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
      samples: this.annotateAuthoritativeHeadways(deduplicatedSamples.map((sample) => ({
        ...sample,
        point: [sample.point[0], sample.point[1], sample.point[2]] as [number, number, number],
        sampleQuality: sample.sampleQuality
          ?? (sample.poseSource === 'held' ? 'held' : 'authoritative'),
      }))),
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
    if (this.frames.length > MAX_SOURCE_FRAMES) {
      this.frames.splice(0, this.frames.length - MAX_SOURCE_FRAMES)
    }
    return true
  }

  sample(wallTimeMs: number): VehicleTwinSample[] | null {
    if (this.frames.length < 2 || !Number.isFinite(wallTimeMs)) return null
    const first = this.frames[0]
    const latest = this.frames.at(-1)!
    const startupBufferSeconds = Math.min(
      this.bufferSeconds,
      this.resolveSourceStepSeconds() * 2,
    )
    if (
      this.renderElapsedSeconds == null
      && latest.elapsedSeconds - first.elapsedSeconds + 1e-9 < startupBufferSeconds
    ) return null

    if (this.renderElapsedSeconds == null) {
      this.renderElapsedSeconds = Math.max(
        first.elapsedSeconds,
        latest.elapsedSeconds - this.bufferSeconds,
      )
      this.lastOutputWallTimeMs = wallTimeMs
    } else {
      const wallDeltaSeconds = this.lastOutputWallTimeMs == null
        ? 0
        : clamp((wallTimeMs - this.lastOutputWallTimeMs) / 1_000, 0, MAX_OUTPUT_STEP_SECONDS)
      this.lastOutputWallTimeMs = wallTimeMs
      const targetElapsedSeconds = latest.elapsedSeconds - this.bufferSeconds
      const timingErrorSeconds = targetElapsedSeconds - this.renderElapsedSeconds
      const rateRatio = clamp(
        1 + timingErrorSeconds / Math.max(0.25, this.bufferSeconds) * 0.1,
        MIN_PLAYBACK_RATE_RATIO,
        MAX_PLAYBACK_RATE_RATIO,
      )
      this.expectedPlaybackRate = this.sourceRate * rateRatio
      this.renderElapsedSeconds += wallDeltaSeconds * this.expectedPlaybackRate
      const nextUnderrunActive = this.renderElapsedSeconds > latest.elapsedSeconds + 1e-6
      if (nextUnderrunActive && !this.underrunActive) this.underrunCount += 1
      this.underrunActive = nextUnderrunActive
    }

    const desired = this.applyPredictionForwardCaps(
      this.interpolateAt(this.renderElapsedSeconds),
    )
    const result = this.stabilizePresentation(desired, this.renderElapsedSeconds)
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
    this.bufferStableSinceMs = null
    this.underrunCount = 0
    this.underrunActive = false
    this.incompatiblePathInterpolationCount = 0
    this.predictedVehicleCount = 0
    this.maximumPredictionSeconds = 0
    this.reconcilingVehicleCount = 0
    this.maximumReconciliationSpeedMetersPerSecond = 0
    this.maximumReconciliationAccelerationMetersPerSecondSquared = 0
    this.movingFreezeFrameCount = 0
    this.batchArrivalCount = 0
    this.vehicleScaleViolationCount = 0
    this.normalTransitionEpochViolationCount = 0
    this.authoritativePositionOverrideCount = 0
    this.backwardArcMovementCount = 0
    this.predictionForwardClampCount = 0
    this.presentedVehicles.clear()
    this.sceneGeneration = null
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
      incompatiblePathInterpolationCount: this.incompatiblePathInterpolationCount,
      targetBufferSeconds: this.targetBufferSeconds,
      expectedPlaybackRate: this.expectedPlaybackRate,
      predictedVehicleCount: this.predictedVehicleCount,
      maximumPredictionSeconds: this.maximumPredictionSeconds,
      reconcilingVehicleCount: this.reconcilingVehicleCount,
      maximumReconciliationSpeedMetersPerSecond: this.maximumReconciliationSpeedMetersPerSecond,
      maximumReconciliationAccelerationMetersPerSecondSquared:
        this.maximumReconciliationAccelerationMetersPerSecondSquared,
      movingFreezeFrameCount: this.movingFreezeFrameCount,
      batchArrivalCount: this.batchArrivalCount,
      vehicleScaleViolationCount: this.vehicleScaleViolationCount,
      normalTransitionEpochViolationCount: this.normalTransitionEpochViolationCount,
      authoritativePositionOverrideCount: this.authoritativePositionOverrideCount,
      backwardArcMovementCount: this.backwardArcMovementCount,
      predictionForwardClampCount: this.predictionForwardClampCount,
    }
  }

  private annotateAuthoritativeHeadways(samples: VehicleTwinSample[]): VehicleTwinSample[] {
    const result = samples.map((sample) => ({
      ...sample,
      authoritativeArcDistanceMeters: sample.sampleQuality === 'authoritative'
        && Number.isFinite(sample.arcDistanceMeters)
        ? Number(sample.arcDistanceMeters)
        : sample.authoritativeArcDistanceMeters,
      authoritativePathArcDistanceMeters: sample.sampleQuality === 'authoritative'
        && Number.isFinite(sample.pathArcDistanceMeters)
        ? Number(sample.pathArcDistanceMeters)
        : sample.authoritativePathArcDistanceMeters,
    }))
    const groups = new Map<string, VehicleTwinSample[]>()
    for (const sample of result) {
      if (
        sample.sampleQuality !== 'authoritative'
        || !sample.occupancyKey
        || !Number.isFinite(sample.arcDistanceMeters)
      ) continue
      const group = groups.get(sample.occupancyKey) ?? []
      group.push(sample)
      groups.set(sample.occupancyKey, group)
    }
    for (const group of groups.values()) {
      group.sort((left, right) => Number(right.arcDistanceMeters) - Number(left.arcDistanceMeters))
      for (let index = 1; index < group.length; index += 1) {
        const leader = group[index - 1]
        const follower = group[index]
        follower.authoritativeLeaderId = leader.id
        follower.authoritativeLeaderGapMeters = Math.max(
          0,
          Number(leader.arcDistanceMeters) - Number(follower.arcDistanceMeters),
        )
      }
    }
    return result
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
      this.bufferStableSinceMs = null
    } else {
      this.bufferStableSinceMs ??= current.arrivalTimeMs
      if (current.arrivalTimeMs - this.bufferStableSinceMs >= BUFFER_SHRINK_STABLE_MS) {
        this.bufferSeconds += (requestedBuffer - this.bufferSeconds) * 0.05
      }
    }
  }

  private resolveSourceStepSeconds(): number {
    if (this.frames.length < 2) return MIN_VEHICLE_BUFFER_SECONDS / 2
    const intervals: number[] = []
    for (let index = 1; index < this.frames.length; index += 1) {
      intervals.push(this.frames[index].elapsedSeconds - this.frames[index - 1].elapsedSeconds)
    }
    return Math.max(0.01, percentile(intervals, 0.5))
  }

  private interpolateAt(elapsedSeconds: number): VehicleTwinSample[] {
    const occurrencesById = new Map<string, TimedVehicleSample[]>()
    for (const frame of this.frames) {
      for (const sample of frame.samples) {
        const occurrences = occurrencesById.get(sample.id) ?? []
        occurrences.push({ frame, sample })
        occurrencesById.set(sample.id, occurrences)
      }
    }
    const samples: VehicleTwinSample[] = []
    for (const occurrences of occurrencesById.values()) {
      const resolved = this.resolveVehicleAt(occurrences, elapsedSeconds)
      if (resolved) samples.push(resolved)
    }
    return samples
  }

  private resolveVehicleAt(
    occurrences: TimedVehicleSample[],
    elapsedSeconds: number,
  ): VehicleTwinSample | null {
    if (occurrences.length === 0) return null
    const lastOccurrence = occurrences.at(-1)!
    const latestFrame = this.frames.at(-1)!
    const sourceRemovalGraceSeconds = this.resolveSourceStepSeconds() * 3 + 1e-6
    if (
      latestFrame.elapsedSeconds - lastOccurrence.frame.elapsedSeconds > sourceRemovalGraceSeconds
      && elapsedSeconds - lastOccurrence.frame.elapsedSeconds > sourceRemovalGraceSeconds
    ) return null
    const authoritative = occurrences.filter(({ sample }) => (
      (sample.sampleQuality ?? 'authoritative') === 'authoritative'
    ))
    const left = authoritative.filter(({ frame }) => frame.elapsedSeconds <= elapsedSeconds).at(-1)
    const right = authoritative.find(({ frame }) => frame.elapsedSeconds >= elapsedSeconds)
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
        return this.cloneSample(
          ratio >= 1 - 1e-9 ? right.sample : left.sample,
          ratio >= 1 - 1e-9 ? 'authoritative' : 'held',
        )
      }
      if (!samplesCanShareMotionCurve(left.sample, right.sample)) {
        this.incompatiblePathInterpolationCount += 1
        return this.cloneSample(ratio >= 1 - 1e-9 ? right.sample : left.sample, 'held')
      }
      const interpolated = interpolateVehicleTwinSample(
        { ...left.sample, time: left.frame.elapsedSeconds * 1_000 },
        { ...right.sample, time: right.frame.elapsedSeconds * 1_000 },
        ratio,
        this.motionPathSampler,
      )
      return { ...interpolated, sampleQuality: 'authoritative', predictionElapsedSeconds: 0 }
    }
    if (left) {
      const previous = authoritative.filter((entry) => (
        entry.frame.elapsedSeconds < left.frame.elapsedSeconds
      )).at(-1)
      const lifecycle = occurrences.filter(({ frame }) => (
        frame.elapsedSeconds <= Math.max(elapsedSeconds, left.frame.elapsedSeconds)
      )).at(-1)
      return this.predictVehicle(left, previous, lifecycle, elapsedSeconds)
    }
    if (right) return this.cloneSample(right.sample, 'authoritative')
    const retained = occurrences.at(-1)!
    return this.cloneSample(
      retained.sample,
      retained.sample.sampleQuality === 'missing' ? 'missing' : 'held',
    )
  }

  private predictVehicle(
    latest: TimedVehicleSample,
    previous: TimedVehicleSample | undefined,
    lifecycle: TimedVehicleSample | undefined,
    elapsedSeconds: number,
  ): VehicleTwinSample {
    const source = latest.sample
    const predictionElapsedSeconds = Math.max(0, elapsedSeconds - latest.frame.elapsedSeconds)
    if (predictionElapsedSeconds <= 1e-9) return this.cloneSample(source, 'authoritative')
    const topologyPrediction = Boolean(
      this.motionPathSampler
      && source.motionPathKey
      && Number.isFinite(source.pathArcDistanceMeters)
      && source.transitionKind !== 'raw_fallback'
      && source.poseSource !== 'raw'
      && source.poseSource !== 'lane_change'
    )
    const predictionWindow = lifecycle?.sample.predictionBlocked
      ? 0
      : topologyPrediction ? TOPOLOGY_PREDICTION_SECONDS : RAW_PREDICTION_SECONDS
    const sourceSpeed = Math.max(0, Number(source.sourceSpeedMetersPerSecond) || 0)
    const measuredSpeed = previous
      ? this.measureVehicleSpeed(previous, latest)
      : 0
    const predictionSpeed = sourceSpeed > MIN_PREDICTION_SPEED_MPS
      && measuredSpeed > MIN_PREDICTION_SPEED_MPS
      ? Math.min(sourceSpeed, measuredSpeed)
      : Math.max(sourceSpeed, measuredSpeed)
    if (predictionSpeed <= MIN_PREDICTION_SPEED_MPS) {
      return {
        ...this.cloneSample(source, 'held'),
        sourceSpeedMetersPerSecond: 0,
        predictionElapsedSeconds,
        stopReason: lifecycle?.sample.stopReason ?? 'source_stopped',
      }
    }
    const constantDuration = Math.min(predictionElapsedSeconds, predictionWindow)
    const decelerationDuration = Math.min(
      Math.max(0, predictionElapsedSeconds - predictionWindow),
      predictionSpeed / MAX_PREDICTION_DECELERATION_MPS2,
    )
    const distanceMeters = predictionSpeed * constantDuration
      + predictionSpeed * decelerationDuration
      - MAX_PREDICTION_DECELERATION_MPS2 * decelerationDuration * decelerationDuration / 2
    const predictedSpeed = Math.max(
      0,
      predictionSpeed - MAX_PREDICTION_DECELERATION_MPS2 * decelerationDuration,
    )
    let predicted = this.cloneSample(source, distanceMeters > 1e-6 ? 'predicted' : 'held')
    if (
      topologyPrediction
      && this.motionPathSampler
      && source.motionPathKey
      && Number.isFinite(source.pathArcDistanceMeters)
    ) {
      const unrestrictedArc = Number(source.pathArcDistanceMeters) + distanceMeters
      const requestedArc = Number.isFinite(source.predictionMaximumPathArcDistanceMeters)
        ? Math.min(unrestrictedArc, Number(source.predictionMaximumPathArcDistanceMeters))
        : unrestrictedArc
      const pathSample = this.motionPathSampler.sample(source.motionPathKey, requestedArc)
      if (pathSample) {
        const modelForwardAxisAngle = Number(source.modelForwardAxisAngle) || 0
        const lateralOffsetMeters = Number(source.sourceLateralOffsetMeters) || 0
        const point = applyLateralOffset(
          [pathSample.longitude, pathSample.latitude, source.point[2]],
          pathSample.heading,
          lateralOffsetMeters,
        )
        const predictedAdvanceMeters = Math.max(
          0,
          pathSample.pathArcDistanceMeters - Number(source.pathArcDistanceMeters),
        )
        predicted = {
          ...predicted,
          point,
          vehicleHeading: pathSample.heading,
          dir: pathSample.heading - modelForwardAxisAngle,
          pathArcDistanceMeters: pathSample.pathArcDistanceMeters,
          arcDistanceMeters: Number.isFinite(source.arcDistanceMeters)
            ? Number(source.arcDistanceMeters) + predictedAdvanceMeters
            : source.arcDistanceMeters,
          sourceSpeedMetersPerSecond: pathSample.pathArcDistanceMeters + 1e-6 < requestedArc
            || requestedArc + 1e-6 < unrestrictedArc
            ? 0
            : predictedSpeed,
        }
      }
    } else {
      const heading = Number.isFinite(source.vehicleHeading)
        ? source.vehicleHeading
        : source.dir + (Number(source.modelForwardAxisAngle) || 0)
      const latitudeRadians = source.point[1] * Math.PI / 180
      predicted.point = [
        source.point[0] + Math.cos(heading) * distanceMeters
          / Math.max(1, Math.cos(latitudeRadians) * 110_900),
        source.point[1] + Math.sin(heading) * distanceMeters / 110_900,
        source.point[2],
      ]
      predicted.arcDistanceMeters = Number.isFinite(source.arcDistanceMeters)
        ? Number(source.arcDistanceMeters) + distanceMeters
        : source.arcDistanceMeters
      predicted.pathArcDistanceMeters = Number.isFinite(source.pathArcDistanceMeters)
        ? Number(source.pathArcDistanceMeters) + distanceMeters
        : source.pathArcDistanceMeters
      predicted.sourceSpeedMetersPerSecond = predictedSpeed
    }
    predicted.predictionElapsedSeconds = predictionElapsedSeconds
    predicted.stopReason = predictedSpeed <= 1e-6
      ? lifecycle?.sample.stopReason ?? 'prediction_decelerated'
      : undefined
    return predicted
  }

  private measureVehicleSpeed(previous: TimedVehicleSample, current: TimedVehicleSample): number {
    const duration = current.frame.elapsedSeconds - previous.frame.elapsedSeconds
    if (duration <= 1e-6 || previous.sample.motionEpoch !== current.sample.motionEpoch) return 0
    if (
      previous.sample.motionPathKey
      && previous.sample.motionPathKey === current.sample.motionPathKey
      && Number.isFinite(previous.sample.pathArcDistanceMeters)
      && Number.isFinite(current.sample.pathArcDistanceMeters)
    ) {
      return clamp(
        (Number(current.sample.pathArcDistanceMeters) - Number(previous.sample.pathArcDistanceMeters)) / duration,
        0,
        60,
      )
    }
    return clamp(this.pointDistanceMeters(previous.sample.point, current.sample.point) / duration, 0, 60)
  }

  private stabilizePresentation(
    desiredSamples: VehicleTwinSample[],
    elapsedSeconds: number,
  ): VehicleTwinSample[] {
    this.predictedVehicleCount = 0
    this.maximumPredictionSeconds = 0
    this.reconcilingVehicleCount = 0
    const activeIds = new Set<string>()
    const output = desiredSamples.map((sourceDesired) => {
      let desired = sourceDesired
      activeIds.add(desired.id)
      if (desired.sampleQuality === 'predicted') {
        this.predictedVehicleCount += 1
        this.maximumPredictionSeconds = Math.max(
          this.maximumPredictionSeconds,
          Number(desired.predictionElapsedSeconds) || 0,
        )
      }
      const previous = this.presentedVehicles.get(desired.id)
      if (previous && previous.sample.sceneGeneration === desired.sceneGeneration) {
        const scaleChanged = desired.scale.some((value, index) => (
          Math.abs(value - previous.sample.scale[index]) > 1e-9
        ))
        if (scaleChanged) this.vehicleScaleViolationCount += 1
        desired = {
          ...desired,
          modelType: previous.sample.modelType,
          scale: [...previous.sample.scale] as [number, number, number],
        }
      }
      if (
        !previous
        || previous.sample.sceneGeneration !== desired.sceneGeneration
        || previous.sample.motionEpoch !== desired.motionEpoch
      ) {
        this.presentedVehicles.set(desired.id, {
          sample: desired,
          elapsedSeconds,
          displaySpeedMetersPerSecond: Math.max(0, Number(desired.sourceSpeedMetersPerSecond) || 0),
          previousTargetSpeedMetersPerSecond: Math.max(0, Number(desired.sourceSpeedMetersPerSecond) || 0),
        })
        return desired
      }
      const deltaSeconds = Math.max(1e-3, elapsedSeconds - previous.elapsedSeconds)
      let reconciliation = previous.reconciliation
      if (
        !reconciliation
        && desired.sampleQuality === 'authoritative'
        && (previous.sample.sampleQuality === 'predicted' || previous.sample.sampleQuality === 'held')
      ) reconciliation = this.createReconciliation(previous.sample, desired, elapsedSeconds)
      const reconciled = reconciliation
        ? this.applyReconciliation(previous.sample, desired, elapsedSeconds, reconciliation)
        : desired
      const movementMeters = this.pointDistanceMeters(previous.sample.point, reconciled.point)
      const targetSpeed = Math.max(0, Number(desired.sourceSpeedMetersPerSecond) || 0)
      if (targetSpeed > 1 && movementMeters < 0.01 && deltaSeconds >= 0.04) {
        this.movingFreezeFrameCount += 1
      }
      const reconciliationFinished = reconciliation
        && elapsedSeconds >= reconciliation.startedAtElapsedSeconds + reconciliation.durationSeconds - 1e-6
      const nextReconciliation = reconciliationFinished ? undefined : reconciliation
      if (nextReconciliation) this.reconcilingVehicleCount += 1
      const nextState: PresentedVehicleState = {
        sample: reconciled,
        elapsedSeconds,
        displaySpeedMetersPerSecond: movementMeters / deltaSeconds,
        previousTargetSpeedMetersPerSecond: targetSpeed,
        reconciliation: nextReconciliation,
      }
      this.presentedVehicles.set(desired.id, nextState)
      return reconciled
    })
    for (const id of this.presentedVehicles.keys()) {
      if (!activeIds.has(id)) this.presentedVehicles.delete(id)
    }
    return output
  }

  private applyPredictionForwardCaps(
    samples: VehicleTwinSample[],
  ): VehicleTwinSample[] {
    const result = samples.map((sample) => ({
      ...sample,
      point: [...sample.point] as [number, number, number],
    }))
    const byId = new Map(result.map((sample) => [sample.id, sample] as const))
    for (const follower of result) {
      if (
        follower.sampleQuality !== 'predicted'
        || !follower.authoritativeLeaderId
        || !Number.isFinite(follower.authoritativeLeaderGapMeters)
        || !Number.isFinite(follower.authoritativeArcDistanceMeters)
        || !Number.isFinite(follower.arcDistanceMeters)
      ) continue
      const leader = byId.get(follower.authoritativeLeaderId)
      if (
        !leader
        || leader.occupancyKey !== follower.occupancyKey
        || !Number.isFinite(leader.arcDistanceMeters)
      ) continue
      const authoritativeArc = Number(follower.authoritativeArcDistanceMeters)
      const maximumArc = Math.max(
        authoritativeArc,
        Number(leader.arcDistanceMeters) - Number(follower.authoritativeLeaderGapMeters),
      )
      const followerArc = Number(follower.arcDistanceMeters)
      if (followerArc <= maximumArc + 1e-6) continue
      const correctionMeters = followerArc - maximumArc
      follower.arcDistanceMeters = maximumArc
      follower.sourceSpeedMetersPerSecond = Math.min(
        Number(follower.sourceSpeedMetersPerSecond) || 0,
        Number(leader.sourceSpeedMetersPerSecond) || 0,
      )
      follower.predictionBlocked = true
      follower.stopReason = 'authoritative_headway_cap'
      this.predictionForwardClampCount += 1
      if (
        this.motionPathSampler
        && follower.motionPathKey
        && Number.isFinite(follower.pathArcDistanceMeters)
      ) {
        const minimumPathArc = Number.isFinite(follower.authoritativePathArcDistanceMeters)
          ? Number(follower.authoritativePathArcDistanceMeters)
          : Number.NEGATIVE_INFINITY
        const requestedPathArc = Math.max(
          minimumPathArc,
          Number(follower.pathArcDistanceMeters) - correctionMeters,
        )
        const pathSample = this.motionPathSampler.sample(follower.motionPathKey, requestedPathArc)
        if (pathSample) {
          const axis = Number(follower.modelForwardAxisAngle) || 0
          follower.point = applyLateralOffset(
            [pathSample.longitude, pathSample.latitude, follower.point[2]],
            pathSample.heading,
            Number(follower.sourceLateralOffsetMeters) || 0,
          )
          follower.pathArcDistanceMeters = pathSample.pathArcDistanceMeters
          follower.vehicleHeading = pathSample.heading
          follower.dir = pathSample.heading - axis
          continue
        }
      }
      const heading = Number.isFinite(follower.vehicleHeading)
        ? follower.vehicleHeading
        : follower.dir + (Number(follower.modelForwardAxisAngle) || 0)
      const latitudeRadians = follower.point[1] * Math.PI / 180
      follower.point = [
        follower.point[0] - Math.cos(heading) * correctionMeters
          / Math.max(1, Math.cos(latitudeRadians) * 110_900),
        follower.point[1] - Math.sin(heading) * correctionMeters / 110_900,
        follower.point[2],
      ]
      if (
        Number.isFinite(follower.authoritativePathArcDistanceMeters)
        && Number.isFinite(follower.pathArcDistanceMeters)
      ) {
        follower.pathArcDistanceMeters = Math.max(
          Number(follower.authoritativePathArcDistanceMeters),
          Number(follower.pathArcDistanceMeters) - correctionMeters,
        )
      }
    }
    return result
  }

  private createReconciliation(
    previous: VehicleTwinSample,
    desired: VehicleTwinSample,
    elapsedSeconds: number,
  ): VehicleReconciliation | undefined {
    const samePath = previous.motionPathKey
      && previous.motionPathKey === desired.motionPathKey
      && Number.isFinite(previous.pathArcDistanceMeters)
      && Number.isFinite(desired.pathArcDistanceMeters)
    const distanceMeters = samePath
      ? Math.abs(Number(previous.pathArcDistanceMeters) - Number(desired.pathArcDistanceMeters))
      : this.pointDistanceMeters(previous.point, desired.point)
    if (distanceMeters < 0.02) return undefined
    const speed = Math.max(1, Number(desired.sourceSpeedMetersPerSecond) || 0)
    const durationSeconds = Math.max(
      MIN_RECONCILIATION_SECONDS,
      distanceMeters * 15 / speed,
      Math.sqrt(distanceMeters * 2),
    )
    const maximumCorrectionSpeed = distanceMeters * 1.5 / durationSeconds
    const maximumCorrectionAcceleration = distanceMeters * 6 / (durationSeconds * durationSeconds)
    this.maximumReconciliationSpeedMetersPerSecond = Math.max(
      this.maximumReconciliationSpeedMetersPerSecond,
      maximumCorrectionSpeed,
    )
    this.maximumReconciliationAccelerationMetersPerSecondSquared = Math.max(
      this.maximumReconciliationAccelerationMetersPerSecondSquared,
      maximumCorrectionAcceleration,
    )
    return samePath
      ? {
          startedAtElapsedSeconds: elapsedSeconds,
          durationSeconds,
          pathOffsetMeters: Number(previous.pathArcDistanceMeters)
            - Number(desired.pathArcDistanceMeters),
        }
      : {
          startedAtElapsedSeconds: elapsedSeconds,
          durationSeconds,
          longitudeOffset: previous.point[0] - desired.point[0],
          latitudeOffset: previous.point[1] - desired.point[1],
        }
  }

  private applyReconciliation(
    previous: VehicleTwinSample,
    desired: VehicleTwinSample,
    elapsedSeconds: number,
    reconciliation: VehicleReconciliation,
  ): VehicleTwinSample {
    const progress = clamp(
      (elapsedSeconds - reconciliation.startedAtElapsedSeconds) / reconciliation.durationSeconds,
      0,
      1,
    )
    const decay = 1 - (3 * progress * progress - 2 * progress * progress * progress)
    if (
      reconciliation.pathOffsetMeters != null
      && this.motionPathSampler
      && desired.motionPathKey
      && Number.isFinite(desired.pathArcDistanceMeters)
    ) {
      const desiredArc = Number(desired.pathArcDistanceMeters)
      const previousArc = Number(previous.pathArcDistanceMeters)
      const requestedArc = Math.max(
        Number.isFinite(previousArc) ? previousArc : 0,
        desiredArc + reconciliation.pathOffsetMeters * decay,
      )
      const pathSample = this.motionPathSampler.sample(desired.motionPathKey, requestedArc)
      if (pathSample) {
        const axis = Number(desired.modelForwardAxisAngle) || 0
        const point = applyLateralOffset(
          [pathSample.longitude, pathSample.latitude, desired.point[2]],
          pathSample.heading,
          Number(desired.sourceLateralOffsetMeters) || 0,
        )
        const pathCorrectionMeters = pathSample.pathArcDistanceMeters - desiredArc
        return {
          ...desired,
          point,
          vehicleHeading: pathSample.heading,
          dir: pathSample.heading - axis,
          pathArcDistanceMeters: pathSample.pathArcDistanceMeters,
          arcDistanceMeters: Number.isFinite(desired.arcDistanceMeters)
            ? Number(desired.arcDistanceMeters) + pathCorrectionMeters
            : desired.arcDistanceMeters,
          reconciling: progress < 1,
        }
      }
    }
    return {
      ...desired,
      point: [
        desired.point[0] + (reconciliation.longitudeOffset ?? 0) * decay,
        desired.point[1] + (reconciliation.latitudeOffset ?? 0) * decay,
        desired.point[2],
      ],
      reconciling: progress < 1,
    }
  }

  private cloneSample(
    sample: VehicleTwinSample,
    sampleQuality: VehicleTwinSample['sampleQuality'],
  ): VehicleTwinSample {
    return {
      ...sample,
      point: [...sample.point] as [number, number, number],
      time: 0,
      sampleQuality,
    }
  }

  private pointDistanceMeters(
    left: readonly [number, number, number],
    right: readonly [number, number, number],
  ): number {
    const latitude = (left[1] + right[1]) / 2 * Math.PI / 180
    return Math.hypot(
      (right[0] - left[0]) * Math.max(1, Math.cos(latitude) * 110_900),
      (right[1] - left[1]) * 110_900,
    )
  }

  private pruneConsumedFrames(): void {
    if (this.renderElapsedSeconds == null) return
    const cutoff = this.renderElapsedSeconds - MAX_VEHICLE_BUFFER_SECONDS - 3
    while (
      this.frames.length > 2
      && this.frames[1].elapsedSeconds <= cutoff
    ) this.frames.shift()
  }
}
