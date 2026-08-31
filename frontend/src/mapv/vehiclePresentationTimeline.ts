import type { SimulationState } from '../types/simulation'
import type { TrafficStateView, TrafficVehicleView } from '../types/traffic'
import { VEHICLE_TWIN_RENDER_DELAY_MS } from './vehicleTwinPresentation.ts'
import { interpolateCanonicalVehiclePosition } from './canonicalVehicleMotion.ts'

export const MIN_SHARED_VEHICLE_DELAY_SECONDS = 3
export const MAX_SHARED_VEHICLE_DELAY_SECONDS = 8
const LOW_BUFFER_SECONDS = 1
const RECOVERY_BUFFER_SECONDS = 1.5
const MAX_PRESENTATION_FRAMES = 256
const MIN_TRACKED_INTERVAL_MS = 20
const MAX_TRACKED_INTERVAL_MS = 10_000
const MAX_NORMAL_PRESENTATION_RATE_RATIO = 1.01
const MAX_RATE_RATIO_DECREASE_PER_SECOND = 0.10
const MAX_RATE_RATIO_INCREASE_PER_SECOND = 0.02
const AUTHORITY_EXHAUSTION_EPSILON_SECONDS = 1e-4
const PRESENTATION_HISTORY_SECONDS = 30
const MAX_TRACKED_SOURCE_RATE = 20
const MAX_PRESENTATION_TICK_SECONDS = 0.05

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
  unresolvedVehicleIds: ReadonlySet<string>
}

export type PresentationState = 'buffering' | 'playing' | 'starved'

export interface PresentationDiagnostics {
  state: PresentationState
  bufferDepthSeconds: number
  rateCorrection: number
  starvationCount: number
  starvationDurationMs: number
}

export interface VehiclePresentationTimelineStats extends PresentationDiagnostics {
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

function interpolateNumber(
  left: number | null | undefined,
  right: number | null | undefined,
  ratio: number,
): number | null {
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
): TrafficVehicleView | null {
  const pickRight = ratio >= 1
  const canonicalPosition = interpolateCanonicalVehiclePosition(left, right, ratio)
  if (
    !canonicalPosition.resolved
    || canonicalPosition.longitude == null
    || canonicalPosition.latitude == null
    || canonicalPosition.sourceX == null
    || canonicalPosition.sourceY == null
  ) {
    const longitude = interpolateNumber(left.longitude, right.longitude, ratio)
    const latitude = interpolateNumber(left.latitude, right.latitude, ratio)
    const x = interpolateNumber(left.x, right.x, ratio)
    const y = interpolateNumber(left.y, right.y, ratio)

    if (
      longitude == null
      || latitude == null
      || x == null
      || y == null
    ) return null

    return {
      ...(pickRight ? right : left),
      longitude,
      latitude,
      x,
      y,
      speed: Math.max(0, left.speed + (right.speed - left.speed) * ratio),
      angle: left.angle + shortestAngleDelta(left.angle, right.angle) * ratio,
      lane_id: ratio < 0.5 ? left.lane_id : right.lane_id,
      acceleration:
        interpolateNumber(left.acceleration, right.acceleration, ratio)
        ?? undefined,
      lane_position:
        interpolateNumber(left.lane_position, right.lane_position, ratio)
        ?? undefined,
      canonical_segment_id: undefined,
      canonical_route_evidence: undefined,
      canonical_heading_radians: undefined,
      canonical_source_x: x,
      canonical_source_y: y,
      canonical_lane_station: undefined,
      canonical_motion_resolved: false,
    }
  }
  const canonicalAngle = canonicalPosition.headingRadians == null
    ? left.angle + shortestAngleDelta(left.angle, right.angle) * ratio
    : ((90 - canonicalPosition.headingRadians * 180 / Math.PI) % 360 + 360) % 360
  return {
    ...(pickRight ? right : left),
    longitude: canonicalPosition.longitude,
    latitude: canonicalPosition.latitude,
    x: canonicalPosition.sourceX,
    y: canonicalPosition.sourceY,
    speed: Math.max(0, left.speed + (right.speed - left.speed) * ratio),
    angle: canonicalAngle,
    lane_id: canonicalPosition.laneId,
    acceleration: interpolateNumber(left.acceleration, right.acceleration, ratio) ?? undefined,
    lane_position: canonicalPosition.laneStation ?? undefined,
    distance: interpolateNumber(left.distance, right.distance, ratio) ?? undefined,
    target_speed: interpolateNumber(left.target_speed, right.target_speed, ratio) ?? undefined,
    canonical_segment_id: canonicalPosition.segmentId,
    canonical_route_evidence: canonicalPosition.routeEvidence === 'unresolved'
      ? undefined
      : canonicalPosition.routeEvidence,
    canonical_heading_radians: canonicalPosition.headingRadians ?? undefined,
    canonical_source_x: canonicalPosition.sourceX,
    canonical_source_y: canonicalPosition.sourceY,
    canonical_lane_station: canonicalPosition.laneStation ?? undefined,
  }
}

function extrapolateDepartingVehicle(
  vehicle: TrafficVehicleView,
  durationSeconds: number,
): TrafficVehicleView {
  if (
    vehicle.longitude == null
    || vehicle.latitude == null
    || !Number.isFinite(vehicle.speed)
    || !Number.isFinite(vehicle.angle)
  ) return vehicle

  const distanceMeters = Math.min(
    15,
    Math.max(0, vehicle.speed) * durationSeconds,
  )
  const heading = (90 - vehicle.angle) * Math.PI / 180
  const latitudeRadians = vehicle.latitude * Math.PI / 180

  return {
    ...vehicle,
    x: vehicle.x + Math.cos(heading) * distanceMeters,
    y: vehicle.y + Math.sin(heading) * distanceMeters,
    longitude:
      vehicle.longitude
      + Math.cos(heading) * distanceMeters
        / Math.max(1, 111_320 * Math.cos(latitudeRadians)),
    latitude:
      vehicle.latitude
      + Math.sin(heading) * distanceMeters / 110_574,
    lane_position: Number.isFinite(vehicle.lane_position)
      ? Number(vehicle.lane_position) + distanceMeters
      : vehicle.lane_position,
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
  private presentationState: PresentationState = 'buffering'
  private presentationRateRatio = 1
  private rateCorrection = 0
  private starvationCount = 0
  private starvationStartedAtMs: number | null = null
  private completedStarvationDurationMs = 0

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

        const effectivePlaybackRate = Math.max(
          1,
          Number(playbackRate) || 1,
        )

        // Twin 的 500ms 是墙钟时间，需要换算为仿真时间。
        // 另外至少预留两个权威快照间隔。
        const twinLeadDelay =
          VEHICLE_TWIN_RENDER_DELAY_MS / 1_000 * effectivePlaybackRate
          + Math.max(1, simulationDeltaSeconds * 2)

        const desiredDelay = Math.min(
          MAX_SHARED_VEHICLE_DELAY_SECONDS,
          Math.max(requestedDelay, twinLeadDelay),
        )
        this.delaySeconds = desiredDelay >= this.delaySeconds
          ? desiredDelay
          : Math.max(desiredDelay, this.delaySeconds - 0.025)
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
    const availableHistorySeconds = latest.elapsedSeconds - first.elapsedSeconds
    if (this.displayElapsedSeconds == null) {
      this.lastTickWallTimeMs = wallTimeMs
      if (availableHistorySeconds + 1e-9 < this.delaySeconds) {
        this.presentationState = 'buffering'
        return null
      }
      this.displayElapsedSeconds = Math.max(first.elapsedSeconds, latest.elapsedSeconds - this.delaySeconds)
      this.presentationState = 'playing'
      this.presentationRateRatio = 1
      this.rateCorrection = 0
      return this.displayElapsedSeconds
    }

    const rawWallDeltaSeconds = this.lastTickWallTimeMs == null
      ? 0
      : Math.max(0, (wallTimeMs - this.lastTickWallTimeMs) / 1_000)
    this.lastTickWallTimeMs = wallTimeMs

    // 主线程发生长任务时，不让车辆在恢复后的单帧中跳过整段时间。
    // 丢失的展示时间转化为额外缓冲延迟，后续缓慢消化。
    const wallDeltaSeconds = Math.min(
      rawWallDeltaSeconds,
      MAX_PRESENTATION_TICK_SECONDS,
    )
    if (latest.state !== 'RUNNING' && latest.state !== 'STARTING' && latest.state !== 'STOPPING') {
      this.displayElapsedSeconds = latest.elapsedSeconds
      this.presentationState = 'playing'
      this.presentationRateRatio = 0
      this.rateCorrection = 0
      return this.displayElapsedSeconds
    }

    let bufferDepthSeconds = latest.elapsedSeconds - this.displayElapsedSeconds
    if (this.presentationState === 'starved') {
      if (bufferDepthSeconds + 1e-9 < RECOVERY_BUFFER_SECONDS) {
        this.presentationRateRatio = 0
        this.rateCorrection = -1
        return this.displayElapsedSeconds
      }

      this.presentationState = 'playing'

      // 已经重新获得完整缓冲，直接恢复正常墙钟速率。
      this.presentationRateRatio = 1
      this.rateCorrection = 0

      if (this.starvationStartedAtMs != null) {
        this.completedStarvationDurationMs += Math.max(
          0,
          wallTimeMs - this.starvationStartedAtMs,
        )
        this.starvationStartedAtMs = null
      }
    }

    const requestedRate = Math.max(0.05, latest.playbackRate || 1)
    const observedSourceRate = percentile(this.sourceRates, 0.5)
    const sustainableRate = observedSourceRate > 0
      ? Math.min(requestedRate * MAX_NORMAL_PRESENTATION_RATE_RATIO, observedSourceRate)
      : requestedRate
    let desiredRateRatio = clamp(
      sustainableRate / requestedRate,
      0.05,
      MAX_NORMAL_PRESENTATION_RATE_RATIO,
    )
    if (bufferDepthSeconds > this.delaySeconds + 0.5) {
      desiredRateRatio = Math.min(MAX_NORMAL_PRESENTATION_RATE_RATIO, Math.max(desiredRateRatio, 1))
    } else if (bufferDepthSeconds < LOW_BUFFER_SECONDS) {
      desiredRateRatio *= clamp(bufferDepthSeconds / LOW_BUFFER_SECONDS, 0, 1)
    }

    const rateDelta = desiredRateRatio - this.presentationRateRatio
    const maximumRateStep = (
      rateDelta < 0
        ? MAX_RATE_RATIO_DECREASE_PER_SECOND
        : MAX_RATE_RATIO_INCREASE_PER_SECOND
    ) * wallDeltaSeconds
    this.presentationRateRatio += clamp(rateDelta, -maximumRateStep, maximumRateStep)
    this.presentationRateRatio = clamp(
      this.presentationRateRatio,
      0,
      MAX_NORMAL_PRESENTATION_RATE_RATIO,
    )
    this.rateCorrection = this.presentationRateRatio - 1

    const proposedElapsedSeconds = this.displayElapsedSeconds
      + wallDeltaSeconds * requestedRate * this.presentationRateRatio
    this.displayElapsedSeconds = Math.min(latest.elapsedSeconds, proposedElapsedSeconds)
    bufferDepthSeconds = latest.elapsedSeconds - this.displayElapsedSeconds
    if (
      proposedElapsedSeconds >= latest.elapsedSeconds - AUTHORITY_EXHAUSTION_EPSILON_SECONDS
      && bufferDepthSeconds <= AUTHORITY_EXHAUSTION_EPSILON_SECONDS
      && wallDeltaSeconds > 0
    ) {
      this.presentationState = 'starved'
      this.presentationRateRatio = 0
      this.rateCorrection = -1
      this.starvationCount += 1
      this.starvationStartedAtMs = wallTimeMs
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
        unresolvedVehicleIds: new Set(),
      }
    }
    const duration = right.elapsedSeconds - left.elapsedSeconds
    const ratio = duration <= 1e-9
      ? 0
      : clamp((elapsedSeconds - left.elapsedSeconds) / duration, 0, 1)
    const rightById = new Map(right.view.vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle] as const))
    const roster = ratio >= 1 ? right.view.vehicles : left.view.vehicles
    const unresolvedVehicleIds = new Set<string>()
    const vehicles = roster.flatMap((vehicle): TrafficVehicleView[] => {
      const rightVehicle = rightById.get(vehicle.vehicle_id)
      if (!rightVehicle) {
        const projectedRight = extrapolateDepartingVehicle(vehicle, duration)
        const projected = interpolateVehicle(vehicle, projectedRight, ratio)
        return [projected ?? vehicle]
      }
      const interpolated = interpolateVehicle(vehicle, rightVehicle, ratio)
      if (interpolated) return [interpolated]
      unresolvedVehicleIds.add(vehicle.vehicle_id)
      return []
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
      unresolvedVehicleIds,
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
    this.presentationState = 'buffering'
    this.presentationRateRatio = 1
    this.rateCorrection = 0
    this.starvationCount = 0
    this.starvationStartedAtMs = null
    this.completedStarvationDurationMs = 0
  }

  stats(): VehiclePresentationTimelineStats {
    const latest = this.frames.at(-1)
    const starvationDurationMs = this.completedStarvationDurationMs + (
      this.starvationStartedAtMs != null && this.lastTickWallTimeMs != null
        ? Math.max(0, this.lastTickWallTimeMs - this.starvationStartedAtMs)
        : 0
    )
    return {
      state: this.presentationState,
      bufferDepthSeconds: latest && this.displayElapsedSeconds != null
        ? Math.max(0, latest.elapsedSeconds - this.displayElapsedSeconds)
        : 0,
      rateCorrection: this.rateCorrection,
      starvationCount: this.starvationCount,
      starvationDurationMs,
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
