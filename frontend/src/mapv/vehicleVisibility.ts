import type { TrafficVehicleView } from '../types/traffic'
import type { RoadCoordinateProjector } from './roadGeometry'

const METERS_PER_DEGREE_LATITUDE = 110_900
const MIN_RENDER_RADIUS_METERS = 420
const MAX_RENDER_RADIUS_METERS = 1_650
const CAMERA_RANGE_FACTOR = 1.2
const EXIT_RADIUS_HYSTERESIS_METERS = 80
export const MAX_ROSTER_CHANGES_PER_SNAPSHOT = 32

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
  private retained = new Map<string, VisibleVehicle>()
  private lastLimit: number | null = null

  select(
    vehicles: TrafficVehicleView[],
    projector: RoadCoordinateProjector,
    cameraCenter: readonly number[],
    cameraRange: number,
    snapshotKey: string,
    limit = MAX_VISIBLE_VEHICLES,
    priority?: (item: VisibleVehicle) => boolean,
  ): VisibleVehicle[] {
    if (cameraCenter.length < 2 || limit <= 0) {
      this.reset()
      return []
    }
    const entryRadius = resolveVehicleRenderRadius(cameraRange)
    const exitRadius = entryRadius + EXIT_RADIUS_HYSTERESIS_METERS
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

    const isPriority = priority ?? (() => false)
    const candidates = [...current.values()]
      .filter((item) => item.distanceMeters <= entryRadius)
      .sort((a, b) => Number(isPriority(b)) - Number(isPriority(a)) || a.distanceMeters - b.distanceMeters)
    if (this.lastLimit == null || !snapshotKey) {
      const desired = candidates.slice(0, limit)
      this.retained = new Map(desired.map((item) => [item.vehicle.vehicle_id, item]))
      this.lastLimit = limit
      return desired
    }
    const selected = [...this.retained.keys()]
      .map((id) => current.get(id))
      .filter((item): item is VisibleVehicle => Boolean(item && item.distanceMeters <= exitRadius))
    const maximumRemovals = this.lastLimit !== limit
      ? MAX_ROSTER_CHANGES_PER_SNAPSHOT
      : Number.POSITIVE_INFINITY
    const excess = Math.max(0, selected.length - limit)
    const removableIds = new Set(selected
      .sort((left, right) => right.distanceMeters - left.distanceMeters)
      .slice(0, Math.min(excess, maximumRemovals))
      .map((item) => item.vehicle.vehicle_id))
    let kept = selected.filter((item) => !removableIds.has(item.vehicle.vehicle_id))
    const selectedIdsAfterRemoval = new Set(kept.map((item) => item.vehicle.vehicle_id))
    const missingPriority = candidates.filter((item) => (
      isPriority(item) && !selectedIdsAfterRemoval.has(item.vehicle.vehicle_id)
    ))
    if (missingPriority.length > 0) {
      const requiredSlots = Math.max(0, kept.length + missingPriority.length - limit)
      if (requiredSlots > 0) {
        const evicted = new Set(kept
          .filter((item) => !isPriority(item))
          .sort((left, right) => right.distanceMeters - left.distanceMeters)
          .slice(0, requiredSlots)
          .map((item) => item.vehicle.vehicle_id))
        kept = kept.filter((item) => !evicted.has(item.vehicle.vehicle_id))
      }
      kept.push(...missingPriority.slice(0, Math.max(0, limit - kept.length)))
    }
    const keptIds = new Set(kept.map((item) => item.vehicle.vehicle_id))
    const maximumAdditions = MAX_ROSTER_CHANGES_PER_SNAPSHOT
    const additions = candidates
      .filter((item) => !keptIds.has(item.vehicle.vehicle_id))
      .slice(0, Math.min(Math.max(0, limit - kept.length), maximumAdditions))
    const next = [...kept, ...additions].sort((a, b) => a.distanceMeters - b.distanceMeters)
    this.retained = new Map(next.map((item) => [item.vehicle.vehicle_id, item]))
    if (next.length === Math.min(limit, candidates.length)) this.lastLimit = limit
    return next
  }

  selectOverview(
    vehicles: TrafficVehicleView[],
    projector: RoadCoordinateProjector,
    snapshotKey: string,
    limit = MAX_VISIBLE_VEHICLES,
    priority?: (item: VisibleVehicle) => boolean,
  ): VisibleVehicle[] {
    if (limit <= 0) {
      this.reset()
      return []
    }
    const current = new Map<string, VisibleVehicle>()
    for (const vehicle of vehicles) {
      if (vehicle.longitude == null || vehicle.latitude == null) continue
      const [longitude, latitude] = projector([vehicle.longitude, vehicle.latitude])
      if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) continue
      current.set(vehicle.vehicle_id, {
        vehicle,
        longitude,
        latitude,
        distanceMeters: 0,
      })
    }
    const isPriority = priority ?? (() => false)
    const all = [...current.values()]
    const retained = [...this.retained.keys()]
      .map((id) => current.get(id))
      .filter((item): item is VisibleVehicle => Boolean(item))
    const retainedIds = new Set(retained.map((item) => item.vehicle.vehicle_id))
    const priorityItems = all
      .filter((item) => isPriority(item) && !retainedIds.has(item.vehicle.vehicle_id))
      .sort((left, right) => left.vehicle.vehicle_id.localeCompare(right.vehicle.vehicle_id))
    let selected = [...retained, ...priorityItems].slice(0, limit)
    const selectedIds = new Set(selected.map((item) => item.vehicle.vehicle_id))
    if (selected.length < limit) {
      const remaining = all.filter((item) => !selectedIds.has(item.vehicle.vehicle_id))
      const distributed = spatiallyDistributeVehicles(remaining, limit - selected.length)
      selected = [...selected, ...distributed]
    }
    this.retained = new Map(selected.map((item) => [item.vehicle.vehicle_id, item]))
    this.lastLimit = snapshotKey ? limit : null
    return selected
  }

  reset(): void {
    this.retained.clear()
    this.lastLimit = null
  }
}

function spatiallyDistributeVehicles(
  vehicles: VisibleVehicle[],
  limit: number,
): VisibleVehicle[] {
  if (vehicles.length <= limit) return vehicles
  const longitudes = vehicles.map((item) => item.longitude)
  const latitudes = vehicles.map((item) => item.latitude)
  const minimumLongitude = Math.min(...longitudes)
  const maximumLongitude = Math.max(...longitudes)
  const minimumLatitude = Math.min(...latitudes)
  const maximumLatitude = Math.max(...latitudes)
  const gridSize = Math.max(1, Math.ceil(Math.sqrt(limit)))
  const longitudeSpan = Math.max(1e-9, maximumLongitude - minimumLongitude)
  const latitudeSpan = Math.max(1e-9, maximumLatitude - minimumLatitude)
  const buckets = new Map<number, VisibleVehicle[]>()
  for (const item of vehicles) {
    const column = Math.min(
      gridSize - 1,
      Math.floor((item.longitude - minimumLongitude) / longitudeSpan * gridSize),
    )
    const row = Math.min(
      gridSize - 1,
      Math.floor((item.latitude - minimumLatitude) / latitudeSpan * gridSize),
    )
    const key = row * gridSize + column
    const bucket = buckets.get(key) ?? []
    bucket.push(item)
    buckets.set(key, bucket)
  }
  const orderedBuckets = [...buckets.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, bucket]) => bucket.sort((left, right) => (
      left.vehicle.vehicle_id.localeCompare(right.vehicle.vehicle_id)
    )))
  const selected: VisibleVehicle[] = []
  for (let index = 0; selected.length < limit; index += 1) {
    let added = false
    for (const bucket of orderedBuckets) {
      const item = bucket[index]
      if (!item) continue
      selected.push(item)
      added = true
      if (selected.length >= limit) break
    }
    if (!added) break
  }
  return selected
}
