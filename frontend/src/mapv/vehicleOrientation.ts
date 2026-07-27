const METERS_PER_DEGREE_LATITUDE = 110_900
const TWO_PI = Math.PI * 2
const TRAJECTORY_MIN_SPEED_MPS = 0.8
const TRAJECTORY_MIN_DISTANCE_METERS = 0.25
const TRAJECTORY_FALLBACK_ERROR_RADIANS = Math.PI / 4

export interface GeographicPoint {
  longitude: number
  latitude: number
}

export function normalizeRadians(angle: number): number {
  const normalized = angle % TWO_PI
  return normalized < 0 ? normalized + TWO_PI : normalized
}

export function sumoAngleToMapHeading(angleDegrees: number): number {
  return normalizeRadians((90 - angleDegrees) * Math.PI / 180)
}

export function shortestAngleDelta(from: number, to: number): number {
  return ((to - from + Math.PI) % TWO_PI + TWO_PI) % TWO_PI - Math.PI
}

export function unwrapHeading(previous: number | null, next: number): number {
  if (previous == null) return next
  return previous + shortestAngleDelta(previous, next)
}

export function geographicDistanceMeters(a: GeographicPoint, b: GeographicPoint): number {
  const latitude = (a.latitude + b.latitude) / 2 * Math.PI / 180
  const east = (b.longitude - a.longitude) * Math.cos(latitude) * METERS_PER_DEGREE_LATITUDE
  const north = (b.latitude - a.latitude) * METERS_PER_DEGREE_LATITUDE
  return Math.hypot(east, north)
}

export function trajectoryHeading(a: GeographicPoint, b: GeographicPoint): number | null {
  const latitude = (a.latitude + b.latitude) / 2 * Math.PI / 180
  const east = (b.longitude - a.longitude) * Math.cos(latitude) * METERS_PER_DEGREE_LATITUDE
  const north = (b.latitude - a.latitude) * METERS_PER_DEGREE_LATITUDE
  if (Math.hypot(east, north) < TRAJECTORY_MIN_DISTANCE_METERS) return null
  return Math.atan2(north, east)
}

export function resolveContinuousVehicleHeading(
  sumoAngleDegrees: number,
  speedMetersPerSecond: number,
  current: GeographicPoint,
  previousPoint: GeographicPoint | null,
  previousHeading: number | null,
): number {
  const sumoHeading = sumoAngleToMapHeading(sumoAngleDegrees)
  let target = sumoHeading
  if (previousPoint && speedMetersPerSecond >= TRAJECTORY_MIN_SPEED_MPS) {
    const movementHeading = trajectoryHeading(previousPoint, current)
    if (
      movementHeading != null
      && Math.abs(shortestAngleDelta(sumoHeading, movementHeading)) > TRAJECTORY_FALLBACK_ERROR_RADIANS
    ) {
      target = movementHeading
    }
  }
  return unwrapHeading(previousHeading, target)
}

export function moveFromFrontBumperToModelCenter(
  point: GeographicPoint,
  vehicleHeading: number,
  frontBumperOffsetMeters: number,
): GeographicPoint {
  const east = Math.cos(vehicleHeading) * frontBumperOffsetMeters
  const north = Math.sin(vehicleHeading) * frontBumperOffsetMeters
  const latitudeRadians = point.latitude * Math.PI / 180
  return {
    longitude: point.longitude - east / (Math.cos(latitudeRadians) * METERS_PER_DEGREE_LATITUDE),
    latitude: point.latitude - north / METERS_PER_DEGREE_LATITUDE,
  }
}
