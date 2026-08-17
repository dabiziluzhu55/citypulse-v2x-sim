import type { SimulationState } from '../types/simulation'
import type { TrafficStateView, TrafficVehicleView } from '../types/traffic'
import { VEHICLE_TWIN_RENDER_DELAY_MS } from './vehicleTwinPresentation.ts'

export const MIN_SHARED_VEHICLE_DELAY_SECONDS = 2
export const MAX_SHARED_VEHICLE_DELAY_SECONDS = 3
const MAX_PRESENTATION_FRAMES = 256
const MIN_TRACKED_INTERVAL_MS = 20
const MAX_TRACKED_INTERVAL_MS = 10_000
const MAX_RECOVERY_RATE_RATIO = 1.05
const PRESENTATION_HISTORY_SECONDS = 30
const MAX_TRACKED_SOURCE_RATE = 20

interface PresentationSourceFrame {
  sequence: number
  elapsedSeconds: number
  arrivalTimeMs: number
  playbackRate: number
  state: SimulationState
  view: TrafficStateView
}

export interface VehiclePresentationSample {
  elapsedSeconds: number
  view: TrafficStateView
  authoritativeVehicleIds: ReadonlySet<string>
}

export interface VehiclePresentationTimelineStats {
  delaySeconds: number
  displayElapsedSeconds: number | null
  sourceFrameCount: number
  sourceGapP99Ms: number
  observedSourceRate: number
  historyReanchorCount: number
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function percentile(values: readonly number[], ratio: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]
}

function shortestAngleDelta(from: number, to: number): number {
  const fullTurn = 360
  return ((to - from + 180) % fullTurn + fullTurn) % fullTurn - 180
}

function interpolateNumber(left: number | null | undefined, right: number | null | undefined, ratio: number): number | null {
  if (typeof left !== 'number' || !Number.isFinite(left)) {
    return typeof right === 'number' && Number.isFinite(right) ? right : null
  }
  if (typeof right !== 'number' || !Number.isFinite(right)) return left
  return left + (right - left) * ratio
}

function interpolateVehicle(
  left: TrafficVehicleView,
  right: TrafficVehicleView,
  ratio: number,
): TrafficVehicleView {
  const pickRight = ratio >= 1
  return {
    ...(pickRight ? right : left),
    longitude: interpolateNumber(left.longitude, right.longitude, ratio),
    latitude: interpolateNumber(left.latitude, right.latitude, ratio),
    x: left.x + (right.x - left.x) * ratio,
    y: left.y + (right.y - left.y) * ratio,
    speed: Math.max(0, left.speed + (right.speed - left.speed) * ratio),
    angle: left.angle + shortestAngleDelta(left.angle, right.angle) * ratio,
    acceleration: interpolateNumber(left.acceleration, right.acceleration, ratio) ?? undefined,
    lane_position: interpolateNumber(left.lane_position, right.lane_position, ratio) ?? undefined,
    distance: interpolateNumber(left.distance, right.distance, ratio) ?? undefined,
    target_speed: interpolateNumber(left.target_speed, right.target_speed, ratio) ?? undefined,
  }
}

export class VehiclePresentationTimeline {
  private sessionId = ''
  private frames: PresentationSourceFrame[] = []
  private sourceIntervalsMs: number[] = []
  private sourceRates: number[] = []
  private delaySeconds = MIN_SHARED_VEHICLE_DELAY_SECONDS
  private displayElapsedSeconds: number | null = null
  private lastTickWallTimeMs: number | null = null
  private historyReanchorCount = 0

  push(
    view: TrafficStateView,
    sequence: number,
    state: SimulationState,
    playbackRate: number | null | undefined,
    arrivalTimeMs: number,
  ): boolean {
    if (!view.session_id || !Number.isFinite(sequence) || !Number.isFinite(view.elapsed_seconds)) return false
    if (this.sessionId && this.sessionId !== view.session_id) this.reset(view.session_id)
    if (!this.sessionId) this.sessionId = view.session_id
    const previous = this.frames.at(-1)
    if (previous && sequence <= previous.sequence) return false
    if (previous) {
      const intervalMs = arrivalTimeMs - previous.arrivalTimeMs
      if (intervalMs >= MIN_TRACKED_INTERVAL_MS && intervalMs <= MAX_TRACKED_INTERVAL_MS) {
        this.sourceIntervalsMs.push(intervalMs)
        this.sourceIntervalsMs = this.sourceIntervalsMs.slice(-30)
        const simulationDeltaSeconds = view.elapsed_seconds - previous.elapsedSeconds
        if (simulationDeltaSeconds > 0) {
          this.sourceRates.push(clamp(
            simulationDeltaSeconds / (intervalMs / 1_000),
            0.05,
            MAX_TRACKED_SOURCE_RATE,
          ))
          this.sourceRates = this.sourceRates.slice(-30)
        }
        const requestedDelay = clamp(
          percentile(this.sourceIntervalsMs, 0.99) / 1_000 * 2,
          MIN_SHARED_VEHICLE_DELAY_SECONDS,
          MAX_SHARED_VEHICLE_DELAY_SECONDS,
        )
        const twinLeadDelay = VEHICLE_TWIN_RENDER_DELAY_MS / 1_000
          * Math.max(1, Number(playbackRate) || 1)
          + 0.5
        this.delaySeconds = Math.max(
          this.delaySeconds,
          Math.min(MAX_SHARED_VEHICLE_DELAY_SECONDS, Math.max(requestedDelay, twinLeadDelay)),
        )
      }
    }
    this.frames.push({
      sequence,
      elapsedSeconds: view.elapsed_seconds,
      arrivalTimeMs,
      playbackRate: Math.max(0, Number(playbackRate) || 0),
      state,
      view,
    })
    this.frames = this.frames.slice(-MAX_PRESENTATION_FRAMES)
    this.prune()
    return true
  }

  tick(wallTimeMs: number): number | null {
    const first = this.frames[0]
    const latest = this.frames.at(-1)
    if (!first || !latest) return null
    const target = latest.elapsedSeconds - this.delaySeconds
    if (target + 1e-9 < first.elapsedSeconds) return null
    if (this.displayElapsedSeconds == null) {
      this.displayElapsedSeconds = Math.max(first.elapsedSeconds, target)
      this.lastTickWallTimeMs = wallTimeMs
      return this.displayElapsedSeconds
    }
    const wallDeltaSeconds = this.lastTickWallTimeMs == null
      ? 0
      : Math.max(0, (wallTimeMs - this.lastTickWallTimeMs) / 1_000)
    this.lastTickWallTimeMs = wallTimeMs
    if (latest.state === 'RUNNING' || latest.state === 'STARTING' || latest.state === 'STOPPING') {
      const observedSourceRate = percentile(this.sourceRates, 0.5)
      const sourceRate = Math.max(0, latest.playbackRate || 1, observedSourceRate)
      const rate = this.displayElapsedSeconds + 1e-6 < target
        ? sourceRate * MAX_RECOVERY_RATE_RATIO
        : sourceRate
      this.displayElapsedSeconds = Math.min(
        target,
        this.displayElapsedSeconds + wallDeltaSeconds * rate,
      )
    } else {
      // No future microsteps will arrive while paused or terminal. Freeze both
      // maps on a transmitted SUMO endpoint instead of an unplayable gap.
      this.displayElapsedSeconds = latest.elapsedSeconds
    }
    return this.displayElapsedSeconds
  }

  sample(): VehiclePresentationSample | null {
    const elapsedSeconds = this.displayElapsedSeconds
    if (elapsedSeconds == null || this.frames.length === 0) return null
    let rightIndex = this.frames.findIndex((frame) => frame.elapsedSeconds > elapsedSeconds)
    if (rightIndex < 0) rightIndex = this.frames.length
    const left = this.frames[Math.max(0, rightIndex - 1)]
    const right = rightIndex < this.frames.length ? this.frames[rightIndex] : null
    if (!left) return null
    if (!right) {
      const ids = new Set(left.view.vehicles.map((vehicle) => vehicle.vehicle_id))
      return {
        elapsedSeconds,
        view: { ...left.view, elapsed_seconds: elapsedSeconds },
        authoritativeVehicleIds: ids,
      }
    }
    const duration = right.elapsedSeconds - left.elapsedSeconds
    const ratio = duration <= 1e-9
      ? 0
      : clamp((elapsedSeconds - left.elapsedSeconds) / duration, 0, 1)
    const rightById = new Map(right.view.vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle] as const))
    const roster = ratio >= 1 ? right.view.vehicles : left.view.vehicles
    const vehicles = roster.map((vehicle) => {
      const rightVehicle = rightById.get(vehicle.vehicle_id)
      return rightVehicle ? interpolateVehicle(vehicle, rightVehicle, ratio) : vehicle
    })
    return {
      elapsedSeconds,
      view: {
        ...left.view,
        elapsed_seconds: elapsedSeconds,
        progress: left.view.duration_seconds > 0
          ? clamp(elapsedSeconds / left.view.duration_seconds, 0, 1)
          : left.view.progress,
        vehicles,
      },
      authoritativeVehicleIds: new Set(roster.map((vehicle) => vehicle.vehicle_id)),
    }
  }

  reset(sessionId = ''): void {
    this.sessionId = sessionId
    this.frames = []
    this.sourceIntervalsMs = []
    this.sourceRates = []
    this.delaySeconds = MIN_SHARED_VEHICLE_DELAY_SECONDS
    this.displayElapsedSeconds = null
    this.lastTickWallTimeMs = null
    this.historyReanchorCount = 0
  }

  stats(): VehiclePresentationTimelineStats {
    return {
      delaySeconds: this.delaySeconds,
      displayElapsedSeconds: this.displayElapsedSeconds,
      sourceFrameCount: this.frames.length,
      sourceGapP99Ms: percentile(this.sourceIntervalsMs, 0.99),
      observedSourceRate: percentile(this.sourceRates, 0.5),
      historyReanchorCount: this.historyReanchorCount,
    }
  }

  private prune(): void {
    const latest = this.frames.at(-1)
    if (!latest) return
    const retainedHistoryCutoff = latest.elapsedSeconds - PRESENTATION_HISTORY_SECONDS
    const displayCutoff = this.displayElapsedSeconds == null
      ? retainedHistoryCutoff
      : this.displayElapsedSeconds - 1
    const cutoff = Math.min(retainedHistoryCutoff, displayCutoff)
    while (this.frames.length > 2 && this.frames[1].elapsedSeconds < cutoff) this.frames.shift()
  }
}
