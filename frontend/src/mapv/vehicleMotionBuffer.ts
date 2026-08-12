import type { VehicleTwinSample } from './vehicleTwinSample'
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
}

interface TimedVehicleSample {
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
  // A label alone is not proof that two paths share physical geometry. The
  // locked SUMO connection is the explicit bridge across lane/path keys.
  return Boolean(
    left.connectionKey
    && right.connectionKey
    && left.connectionKey === right.connectionKey
    && right.roadTransitionKind === 'topology_successor',
  )
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
    authoritativeSourceTimeSeconds:
      (Number(left.authoritativeSourceTimeSeconds) || left.time / 1_000)
      + (
        (Number(right.authoritativeSourceTimeSeconds) || right.time / 1_000)
        - (Number(left.authoritativeSourceTimeSeconds) || left.time / 1_000)
      ) * amount,
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
  private sceneGeneration: number | null = null
  private motionPathSampler: MotionPathSampler | null = null

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
    if (this.frames.length > MAX_SOURCE_FRAMES) {
      this.frames.splice(0, this.frames.length - MAX_SOURCE_FRAMES)
    }
    return true
  }

  sample(wallTimeMs: number): VehicleTwinSample[] | null {
    if (this.frames.length < 2 || !Number.isFinite(wallTimeMs)) return null
    const first = this.frames[0]
    const latest = this.frames.at(-1)!
    const startupBufferSeconds = this.bufferSeconds
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
      this.renderElapsedSeconds = Math.min(this.renderElapsedSeconds, latest.elapsedSeconds)
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
        this.incompatiblePathInterpolationBlockedCount += 1
        return this.cloneSample(ratio >= 1 - 1e-9 ? right.sample : left.sample, 'held')
      }
      const interpolated = interpolateVehicleTwinSample(
        { ...left.sample, time: left.frame.elapsedSeconds * 1_000 },
        { ...right.sample, time: right.frame.elapsedSeconds * 1_000 },
        ratio,
        this.motionPathSampler,
      )
      this.authoritativeInterpolationCount += 1
      return {
        ...interpolated,
        sampleQuality: 'authoritative',
        predictionElapsedSeconds: 0,
        displayElapsedSeconds: elapsedSeconds,
      }
    }
    if (left) return this.cloneSample(left.sample, 'authoritative', elapsedSeconds)
    if (right) return this.cloneSample(right.sample, 'held', elapsedSeconds)
    const retained = occurrences.at(-1)!
    return this.cloneSample(
      retained.sample,
      retained.sample.sampleQuality === 'missing' ? 'missing' : 'held',
      elapsedSeconds,
    )
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
    while (
      this.frames.length > 2
      && this.frames[1].elapsedSeconds <= cutoff
    ) this.frames.shift()
  }
}
