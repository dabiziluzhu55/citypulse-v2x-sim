import type { TrafficVehicleView } from '../types/traffic'
import type { RoadCoordinateProjector } from './roadGeometry'

const METERS_PER_DEGREE_LATITUDE = 110_900
const MIN_RENDER_RADIUS_METERS = 420
const MAX_RENDER_RADIUS_METERS = 1_650
const CAMERA_RANGE_FACTOR = 1.2

export const MAX_VISIBLE_VEHICLES = 450

export interface VisibleVehicle {
  vehicle: TrafficVehicleView
  longitude: number
  latitude: number
  distanceMeters: number
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
