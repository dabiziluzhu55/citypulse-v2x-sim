export type IntersectionRoadLod = 'overview' | 'medium' | 'full'

export const INTERSECTION_ROAD_LOD = {
  mediumEnterRangeMeters: 8_000,
  fullEnterRangeMeters: 2_000,
  fullEnterDistanceMeters: 2_500,
  hysteresisRatio: 0.15,
} as const

export interface IntersectionRoadLodInput {
  cameraRangeMeters: number
  distanceMeters: number
  active: boolean
  previous?: IntersectionRoadLod
}

export function resolveIntersectionRoadLod(input: IntersectionRoadLodInput): IntersectionRoadLod {
  if (input.active) return 'full'
  const range = Number.isFinite(input.cameraRangeMeters)
    ? Math.max(0, input.cameraRangeMeters)
    : Number.POSITIVE_INFINITY
  const distance = Number.isFinite(input.distanceMeters)
    ? Math.max(0, input.distanceMeters)
    : Number.POSITIVE_INFINITY
  const hysteresis = 1 + INTERSECTION_ROAD_LOD.hysteresisRatio

  const fullFactor = input.previous === 'full' ? hysteresis : 1
  if (
    range <= INTERSECTION_ROAD_LOD.fullEnterRangeMeters * fullFactor
    && distance <= INTERSECTION_ROAD_LOD.fullEnterDistanceMeters * fullFactor
  ) return 'full'

  const mediumFactor = input.previous === 'overview' || input.previous == null ? 1 : hysteresis
  const mediumRange = INTERSECTION_ROAD_LOD.mediumEnterRangeMeters * mediumFactor
  const visibleRadius = Math.max(4_000, range * 1.2) * mediumFactor
  if (range <= mediumRange && distance <= visibleRadius) return 'medium'
  return 'overview'
}
