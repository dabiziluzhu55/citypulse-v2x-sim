const METERS_PER_DEGREE_LATITUDE = 110_900
const TWO_PI = Math.PI * 2
const TRAJECTORY_MIN_SPEED_MPS = 0.8
const TRAJECTORY_MIN_DISTANCE_METERS = 0.25
const STOP_SPEED_MPS = 0.35
const START_SPEED_MPS = 0.8
export const MAX_LANE_MOVEMENT_HEADING_DELTA = Math.PI / 4
export const MAX_VEHICLE_HEADING_RATE = 120 * Math.PI / 180
export const MAX_UNSUPPORTED_RAW_HEADING_JUMP = Math.PI / 2

export interface GeographicPoint {
  longitude: number
  latitude: number
}

export interface VehicleHeadingState {
  point: GeographicPoint
  heading: number
  reliableHeading: number
  moving: boolean
  timeSeconds: number
}

export interface StableVehicleHeadingInput {
  sourceMapHeading?: number | null
  speedMetersPerSecond: number
  current: GeographicPoint
  timeSeconds: number
  laneHeading?: number | null
  topologyConfirmed?: boolean
}

export interface StableVehicleHeadingResult {
  heading: number
  state: VehicleHeadingState
}

export function normalizeRadians(angle: number): number {
  const normalized = angle % TWO_PI
  return normalized < 0 ? normalized + TWO_PI : normalized
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
  sourceMapHeading: number,
  speedMetersPerSecond: number,
  current: GeographicPoint,
  previousPoint: GeographicPoint | null,
  previousHeading: number | null,
): number {
  let target = sourceMapHeading
  if (previousPoint && speedMetersPerSecond >= TRAJECTORY_MIN_SPEED_MPS) {
    const movementHeading = trajectoryHeading(previousPoint, current)
    if (movementHeading != null) target = movementHeading
  }
  return unwrapHeading(previousHeading, target)
}

export function resolveStableVehicleHeading(
  input: StableVehicleHeadingInput,
  previous: VehicleHeadingState | null,
): StableVehicleHeadingResult {
  const sourceHeading = input.sourceMapHeading != null
    && Number.isFinite(input.sourceMapHeading)
    ? normalizeRadians(input.sourceMapHeading)
    : null
  if (!previous) {
    const initial = input.speedMetersPerSecond > STOP_SPEED_MPS
      && input.topologyConfirmed
      && input.laneHeading != null
      ? input.laneHeading
      : sourceHeading ?? input.laneHeading ?? 0
    const heading = normalizeRadians(initial)
    return {
      heading,
      state: {
        point: input.current,
        heading,
        reliableHeading: heading,
        moving: input.speedMetersPerSecond >= START_SPEED_MPS,
        timeSeconds: input.timeSeconds,
      },
    }
  }

  const movementHeading = trajectoryHeading(previous.point, input.current)
  const moving = previous.moving
    ? input.speedMetersPerSecond > STOP_SPEED_MPS && movementHeading != null
    : input.speedMetersPerSecond >= START_SPEED_MPS && movementHeading != null
  let target = previous.reliableHeading
  let topologyHeading = false
  if (
    moving
    && input.topologyConfirmed
    && input.laneHeading != null
    && Number.isFinite(input.laneHeading)
  ) {
    target = input.laneHeading
    topologyHeading = true
  } else if (moving && sourceHeading != null) {
    const unsupportedJump = Math.abs(shortestAngleDelta(previous.reliableHeading, sourceHeading))
      > MAX_UNSUPPORTED_RAW_HEADING_JUMP
      && movementHeading != null
      && Math.abs(shortestAngleDelta(sourceHeading, movementHeading)) > MAX_LANE_MOVEMENT_HEADING_DELTA
    target = unsupportedJump ? previous.reliableHeading : sourceHeading
  } else if (!moving) {
    target = previous.reliableHeading
  }
  const elapsedSeconds = Math.max(0, input.timeSeconds - previous.timeSeconds)
  const targetDelta = shortestAngleDelta(previous.heading, target)
  const maximumTurn = MAX_VEHICLE_HEADING_RATE * elapsedSeconds
  const heading = previous.heading + (topologyHeading
    ? targetDelta
    : Math.max(-maximumTurn, Math.min(maximumTurn, targetDelta)))
  const reliableHeading = topologyHeading || moving
    ? heading
    : previous.reliableHeading
  return {
    heading,
    state: {
      point: input.current,
      heading,
      reliableHeading,
      moving,
      timeSeconds: input.timeSeconds,
    },
  }
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
