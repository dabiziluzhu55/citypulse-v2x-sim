const METERS_PER_DEGREE_LATITUDE = 110_900
const TWO_PI = Math.PI * 2
const TRAJECTORY_MIN_SPEED_MPS = 0.8
const TRAJECTORY_MIN_DISTANCE_METERS = 0.25
const STOP_SPEED_MPS = 0.35
const START_SPEED_MPS = 0.8
export const MAX_LANE_MOVEMENT_HEADING_DELTA = Math.PI / 4
export const MAX_SOURCE_MOVEMENT_HEADING_DELTA = 30 * Math.PI / 180
export const MAX_VEHICLE_HEADING_RATE = 120 * Math.PI / 180

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
  const laneHeading = input.laneHeading != null
    && Number.isFinite(input.laneHeading)
    ? normalizeRadians(input.laneHeading)
    : null

  if (!previous) {
    const initial = input.speedMetersPerSecond > STOP_SPEED_MPS
      && input.topologyConfirmed
      && laneHeading != null
      ? laneHeading
      : sourceHeading ?? laneHeading ?? 0
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
  const topologyOnlyMotion = Boolean(
    movementHeading == null
    && input.speedMetersPerSecond > STOP_SPEED_MPS
    && input.topologyConfirmed
    && laneHeading != null
  )
  let target = previous.reliableHeading
  let topologyHeading = false
  const laneAgreesWithMovement = laneHeading != null
    && movementHeading != null
    && Math.abs(shortestAngleDelta(laneHeading, movementHeading))
      <= MAX_LANE_MOVEMENT_HEADING_DELTA
  if (
    moving
    && input.topologyConfirmed
    && laneHeading != null
    && laneAgreesWithMovement
  ) {
    // Trust a topology tangent only when the observed displacement confirms it.
    target = laneHeading
    topologyHeading = true
  } else if (moving && movementHeading != null) {
    const sourceAgreesWithMovement = sourceHeading != null
      && Math.abs(shortestAngleDelta(sourceHeading, movementHeading))
        <= MAX_SOURCE_MOVEMENT_HEADING_DELTA
    // Prefer observed displacement when raw telemetry points away from motion.
    target = sourceAgreesWithMovement && sourceHeading != null
      ? sourceHeading
      : movementHeading
  } else if (topologyOnlyMotion && laneHeading != null) {
    // The speed still indicates motion but the authoritative displacement is
    // too short to yield a stable trajectory heading. Follow confirmed lane
    // topology through the ordinary turn-rate limiter.
    target = laneHeading
  } else {
    // Hold the last reliable heading while stopped to avoid low-speed jitter.
    target = previous.reliableHeading
  }
  const elapsedSeconds = Math.max(0, input.timeSeconds - previous.timeSeconds)
  const targetDelta = shortestAngleDelta(previous.heading, target)
  const maximumTurn = MAX_VEHICLE_HEADING_RATE * elapsedSeconds
  const heading = previous.heading + (topologyHeading
    ? targetDelta
    : Math.max(-maximumTurn, Math.min(maximumTurn, targetDelta)))
  const reliableHeading = topologyHeading || moving || topologyOnlyMotion
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
