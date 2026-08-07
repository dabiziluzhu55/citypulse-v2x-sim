import type { TrafficVehicleView } from '../types/traffic'
import type { RoadCoordinateProjector } from './roadGeometry'

const METERS_PER_DEGREE_LATITUDE = 110_900
const MIN_RENDER_RADIUS_METERS = 420
const MAX_RENDER_RADIUS_METERS = 1_650
const CAMERA_RANGE_FACTOR = 1.2
const EXIT_RADIUS_HYSTERESIS_METERS = 80
// Keep a vehicle alive through short WebSocket gaps and camera-boundary jitter.
// At the configured 500 ms snapshot cadence this is roughly 2.5 seconds.
export const MISSING_SNAPSHOT_GRACE = 5

export const MAX_VISIBLE_VEHICLES = 450
export const BALANCED_VISIBLE_VEHICLES = 320
export const CONSTRAINED_VISIBLE_VEHICLES = 220

export type VehicleRenderQuality = 'full' | 'balanced' | 'constrained'

const VEHICLE_LIMITS: Record<VehicleRenderQuality, number> = {
  full: MAX_VISIBLE_VEHICLES,
  balanced: BALANCED_VISIBLE_VEHICLES,
  constrained: CONSTRAINED_VISIBLE_VEHICLES,
}

export interface VehicleRenderBudgetState {
  quality: VehicleRenderQuality
  limit: number
  fps: number | null
}

export class AdaptiveVehicleRenderBudget {
  private quality: VehicleRenderQuality = 'full'
  private lastFrameTimeMs: number | null = null
  private frameIntervalsMs: number[] = []
  private lastEvaluationMs = 0
  private degradeSamples = 0
  private recoverSamples = 0
  private fps: number | null = null

  recordFrame(wallTimeMs: number): VehicleRenderBudgetState {
    if (!Number.isFinite(wallTimeMs)) return this.state()
    if (this.lastFrameTimeMs != null) {
      const interval = wallTimeMs - this.lastFrameTimeMs
      if (interval > 0 && interval < 500) {
        this.frameIntervalsMs.push(interval)
        this.frameIntervalsMs = this.frameIntervalsMs.slice(-120)
      }
    }
    this.lastFrameTimeMs = wallTimeMs
    if (wallTimeMs - this.lastEvaluationMs < 1_000 || this.frameIntervalsMs.length < 12) {
      return this.state()
    }
    this.lastEvaluationMs = wallTimeMs
    const p90 = percentile(this.frameIntervalsMs, 0.9)
    this.fps = p90 > 0 ? Math.round(1_000 / p90) : null
    const desired: VehicleRenderQuality = this.fps != null && this.fps < 28
      ? 'constrained'
      : this.fps != null && this.fps < 45
        ? 'balanced'
        : 'full'
    const ranks: Record<VehicleRenderQuality, number> = { full: 0, balanced: 1, constrained: 2 }
    if (ranks[desired] > ranks[this.quality]) {
      this.degradeSamples += 1
      this.recoverSamples = 0
      if (this.degradeSamples >= 3) {
        this.quality = desired
        this.degradeSamples = 0
      }
    } else if (ranks[desired] < ranks[this.quality]) {
      this.recoverSamples += 1
      this.degradeSamples = 0
      if (this.recoverSamples >= 8) {
        this.quality = desired
        this.recoverSamples = 0
      }
    } else {
      this.degradeSamples = 0
      this.recoverSamples = 0
    }
    return this.state()
  }

  state(): VehicleRenderBudgetState {
    return { quality: this.quality, limit: VEHICLE_LIMITS[this.quality], fps: this.fps }
  }

  reset(): void {
    this.quality = 'full'
    this.lastFrameTimeMs = null
    this.frameIntervalsMs = []
    this.lastEvaluationMs = 0
    this.degradeSamples = 0
    this.recoverSamples = 0
    this.fps = null
  }
}

export interface VisibleVehicle {
  vehicle: TrafficVehicleView
  longitude: number
  latitude: number
  distanceMeters: number
}

interface RetainedVehicle {
  visible: VisibleVehicle
  missingSnapshots: number
}

function percentile(values: number[], ratio: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]
}

export function resolveVehicleRenderRadius(cameraRange: number): number {
  const range = Number.isFinite(cameraRange) ? cameraRange : MIN_RENDER_RADIUS_METERS
  return Math.min(
    MAX_RENDER_RADIUS_METERS,
    Math.max(MIN_RENDER_RADIUS_METERS, range * CAMERA_RANGE_FACTOR),
  )
}

export function distanceMeters(
  a: readonly number[],
  b: readonly number[],
): number {
  const latitude = (a[1] + b[1]) / 2 * Math.PI / 180
  const dx = (a[0] - b[0]) * Math.cos(latitude) * METERS_PER_DEGREE_LATITUDE
  const dy = (a[1] - b[1]) * METERS_PER_DEGREE_LATITUDE
  return Math.hypot(dx, dy)
}

export function selectVisibleVehicles(
  vehicles: TrafficVehicleView[],
  projector: RoadCoordinateProjector,
  cameraCenter: readonly number[],
  cameraRange: number,
  limit = MAX_VISIBLE_VEHICLES,
): VisibleVehicle[] {
  if (cameraCenter.length < 2 || limit <= 0) return []
  const radius = resolveVehicleRenderRadius(cameraRange)
  const visible: VisibleVehicle[] = []

  for (const vehicle of vehicles) {
    if (vehicle.longitude == null || vehicle.latitude == null) continue
    const [longitude, latitude] = projector([vehicle.longitude, vehicle.latitude])
    const distance = distanceMeters(cameraCenter, [longitude, latitude])
    if (distance > radius) continue
    visible.push({ vehicle, longitude, latitude, distanceMeters: distance })
  }

  visible.sort((a, b) => a.distanceMeters - b.distanceMeters)
  return visible.slice(0, limit)
}

export class StableVehicleSelector {
  private retained = new Map<string, RetainedVehicle>()
  private lastSnapshotKey = ''

  select(
    vehicles: TrafficVehicleView[],
    projector: RoadCoordinateProjector,
    cameraCenter: readonly number[],
    cameraRange: number,
    snapshotKey: string,
    limit = MAX_VISIBLE_VEHICLES,
  ): VisibleVehicle[] {
    if (cameraCenter.length < 2 || limit <= 0) {
      this.reset()
      return []
    }
    const entryRadius = resolveVehicleRenderRadius(cameraRange)
    const exitRadius = entryRadius + EXIT_RADIUS_HYSTERESIS_METERS
    const advancesSnapshot = snapshotKey !== this.lastSnapshotKey
    this.lastSnapshotKey = snapshotKey
    const current = new Map<string, VisibleVehicle>()

    for (const vehicle of vehicles) {
      if (vehicle.longitude == null || vehicle.latitude == null) continue
      const [longitude, latitude] = projector([vehicle.longitude, vehicle.latitude])
      current.set(vehicle.vehicle_id, {
        vehicle,
        longitude,
        latitude,
        distanceMeters: distanceMeters(cameraCenter, [longitude, latitude]),
      })
    }

    const selected: VisibleVehicle[] = []
    const nextRetained = new Map<string, RetainedVehicle>()
    for (const [id, retained] of this.retained) {
      const candidate = current.get(id)
      if (candidate) {
        if (candidate.distanceMeters > exitRadius) continue
        selected.push(candidate)
        nextRetained.set(id, { visible: candidate, missingSnapshots: 0 })
        continue
      }
      const missingSnapshots = retained.missingSnapshots + (advancesSnapshot ? 1 : 0)
      const distance = distanceMeters(cameraCenter, [retained.visible.longitude, retained.visible.latitude])
      if (missingSnapshots > MISSING_SNAPSHOT_GRACE || distance > exitRadius) continue
      const visible = { ...retained.visible, distanceMeters: distance }
      selected.push(visible)
      nextRetained.set(id, { visible, missingSnapshots })
    }

    selected.sort((a, b) => a.distanceMeters - b.distanceMeters)
    if (selected.length > limit) selected.length = limit
    const selectedIds = new Set(selected.map((item) => item.vehicle.vehicle_id))
    const entrants = [...current.values()]
      .filter((item) => item.distanceMeters <= entryRadius && !selectedIds.has(item.vehicle.vehicle_id))
      .sort((a, b) => a.distanceMeters - b.distanceMeters)

    for (const candidate of entrants) {
      if (selected.length >= limit) break
      selected.push(candidate)
      nextRetained.set(candidate.vehicle.vehicle_id, { visible: candidate, missingSnapshots: 0 })
    }
    for (const id of [...nextRetained.keys()]) {
      if (!selected.some((item) => item.vehicle.vehicle_id === id)) nextRetained.delete(id)
    }
    this.retained = nextRetained
    return selected
  }

  reset(): void {
    this.retained.clear()
    this.lastSnapshotKey = ''
  }
}
