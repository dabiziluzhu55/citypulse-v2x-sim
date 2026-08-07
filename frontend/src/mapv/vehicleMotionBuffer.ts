import type { VehicleTwinSample } from './vehicleTwinSample'

export const MIN_VEHICLE_BUFFER_SECONDS = 0.5
export const MAX_VEHICLE_BUFFER_SECONDS = 2
const BUFFER_INTERVAL_MULTIPLIER = 3
const MAX_OUTPUT_STEP_SECONDS = 0.25
const MAX_PRESENTATION_STEP_SECONDS = 0.25
const MAX_TIMING_SAMPLES = 30
const MAX_SOURCE_FRAMES = 40
const MAX_MEASURED_RATE = 8
const MAX_TRACKED_SOURCE_GAP_MS = 10_000
const METERS_PER_DEGREE_LATITUDE = 110_900
const MIN_MOVEMENT_HEADING_DISTANCE_METERS = 0.04

export interface VehicleMotionSourceFrame {
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

function movementHeading(
  left: VehicleTwinSample,
  right: VehicleTwinSample,
): number | null {
  const latitude = (left.point[1] + right.point[1]) / 2 * Math.PI / 180
  const east = (right.point[0] - left.point[0])
    * Math.cos(latitude)
    * METERS_PER_DEGREE_LATITUDE
  const north = (right.point[1] - left.point[1]) * METERS_PER_DEGREE_LATITUDE
  if (Math.hypot(east, north) < MIN_MOVEMENT_HEADING_DISTANCE_METERS) return null
  return Math.atan2(north, east)
}

export function interpolateVehicleTwinSample(
  left: VehicleTwinSample,
  right: VehicleTwinSample,
  ratio: number,
): VehicleTwinSample {
  const amount = clamp(ratio, 0, 1)
  const headingFromMovement = movementHeading(left, right)
  const leftAxis = Number.isFinite(left.modelForwardAxisAngle) ? left.modelForwardAxisAngle : 0
  const rightAxis = Number.isFinite(right.modelForwardAxisAngle) ? right.modelForwardAxisAngle : 0
  const leftHeading = Number.isFinite(left.vehicleHeading) ? left.vehicleHeading : left.dir + leftAxis
  const rightHeading = Number.isFinite(right.vehicleHeading) ? right.vehicleHeading : right.dir + rightAxis
  const vehicleHeading = headingFromMovement ?? (
    leftHeading
    + shortestAngleDelta(leftHeading, rightHeading) * amount
  )
  const modelForwardAxisAngle = amount < 0.5
    ? leftAxis
    : rightAxis
  return {
    ...(amount < 0.5 ? left : right),
    id: left.id,
    point: [
      left.point[0] + (right.point[0] - left.point[0]) * amount,
      left.point[1] + (right.point[1] - left.point[1]) * amount,
      left.point[2] + (right.point[2] - left.point[2]) * amount,
    ],
    dir: vehicleHeading - modelForwardAxisAngle,
    vehicleHeading,
    modelForwardAxisAngle,
    time: 0,
  }
}

export class VehicleMotionBuffer {
  private frames: VehicleMotionSourceFrame[] = []
  private arrivalIntervalsMs: number[] = []
  private renderElapsedSeconds: number | null = null
  private lastOutputWallTimeMs: number | null = null
  private sourceRate = 1
  private bufferSeconds = MIN_VEHICLE_BUFFER_SECONDS
  private underrunCount = 0
  private underrunActive = false

  push(frame: VehicleMotionSourceFrame): boolean {
    if (
      !Number.isFinite(frame.sequence)
      || !Number.isFinite(frame.elapsedSeconds)
      || !Number.isFinite(frame.arrivalTimeMs)
    ) return false

    const normalizedFrame = {
      ...frame,
      samples: frame.samples.map((sample) => ({
        ...sample,
        point: [sample.point[0], sample.point[1], sample.point[2]] as [number, number, number],
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
    if (
      this.renderElapsedSeconds == null
      && latest.elapsedSeconds - first.elapsedSeconds + 1e-9 < this.bufferSeconds
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
      const sourceStep = this.resolveSourceStepSeconds()
      const maximumTarget = Math.max(first.elapsedSeconds, latest.elapsedSeconds - sourceStep)
      const presentationStep = Math.min(
        wallDeltaSeconds * this.sourceRate,
        MAX_PRESENTATION_STEP_SECONDS,
      )
      const requestedTarget = this.renderElapsedSeconds + presentationStep
      const nextUnderrunActive = requestedTarget > maximumTarget + 1e-6
      if (nextUnderrunActive && !this.underrunActive) this.underrunCount += 1
      this.underrunActive = nextUnderrunActive
      this.renderElapsedSeconds = Math.min(
        maximumTarget,
        requestedTarget,
      )
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
    this.renderElapsedSeconds = null
    this.lastOutputWallTimeMs = null
    this.sourceRate = 1
    this.bufferSeconds = MIN_VEHICLE_BUFFER_SECONDS
    this.underrunCount = 0
    this.underrunActive = false
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
    }
  }

  private updateTiming(
    previous: VehicleMotionSourceFrame,
    current: VehicleMotionSourceFrame,
  ): void {
    const wallDeltaMs = current.arrivalTimeMs - previous.arrivalTimeMs
    const simulationDelta = current.elapsedSeconds - previous.elapsedSeconds
    if (wallDeltaMs <= 0 || wallDeltaMs > MAX_TRACKED_SOURCE_GAP_MS || simulationDelta <= 0) {
      this.arrivalIntervalsMs = []
      return
    }
    const measuredRate = clamp(simulationDelta / (wallDeltaMs / 1_000), 0, MAX_MEASURED_RATE)
    const blend = Math.abs(measuredRate - this.sourceRate) > Math.max(0.75, this.sourceRate * 0.5)
      ? 0.55
      : 0.2
    this.sourceRate += (measuredRate - this.sourceRate) * blend
    this.arrivalIntervalsMs.push(wallDeltaMs)
    this.arrivalIntervalsMs = this.arrivalIntervalsMs.slice(-MAX_TIMING_SAMPLES)
    const p95IntervalSeconds = percentile(this.arrivalIntervalsMs, 0.95) / 1_000
    const p99IntervalSeconds = percentile(this.arrivalIntervalsMs, 0.99) / 1_000
    const jitterIntervalSeconds = Math.max(
      p95IntervalSeconds * BUFFER_INTERVAL_MULTIPLIER,
      p99IntervalSeconds * 2,
    )
    const requestedBuffer = jitterIntervalSeconds * this.sourceRate
    this.bufferSeconds += (
      clamp(
        requestedBuffer,
        MIN_VEHICLE_BUFFER_SECONDS,
        MAX_VEHICLE_BUFFER_SECONDS,
      ) - this.bufferSeconds
    ) * 0.2
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
    let rightIndex = this.frames.findIndex((frame) => frame.elapsedSeconds >= elapsedSeconds)
    if (rightIndex < 0) rightIndex = this.frames.length - 1
    const leftIndex = Math.max(0, rightIndex - 1)
    const left = this.frames[leftIndex]
    const right = this.frames[rightIndex]
    const duration = right.elapsedSeconds - left.elapsedSeconds
    const ratio = duration > 1e-9 ? (elapsedSeconds - left.elapsedSeconds) / duration : 0
    const leftById = new Map(left.samples.map((sample) => [sample.id, sample]))
    const rightById = new Map(right.samples.map((sample) => [sample.id, sample]))
    const ids = new Set([...leftById.keys(), ...rightById.keys()])
    const samples: VehicleTwinSample[] = []
    for (const id of ids) {
      const leftSample = leftById.get(id)
      const rightSample = rightById.get(id)
      if (leftSample && rightSample) {
        samples.push(interpolateVehicleTwinSample(leftSample, rightSample, ratio))
      } else if (leftSample) {
        samples.push({
          ...leftSample,
          point: [leftSample.point[0], leftSample.point[1], leftSample.point[2]],
          time: 0,
        })
      } else if (rightSample) {
        samples.push({
          ...rightSample,
          point: [rightSample.point[0], rightSample.point[1], rightSample.point[2]],
          time: 0,
        })
      }
    }
    return samples
  }

  private pruneConsumedFrames(): void {
    if (this.renderElapsedSeconds == null) return
    while (
      this.frames.length > 2
      && this.frames[1].elapsedSeconds <= this.renderElapsedSeconds
    ) this.frames.shift()
  }
}
