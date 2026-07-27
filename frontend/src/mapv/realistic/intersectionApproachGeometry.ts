import type { Point2, RealisticLane } from './intersectionManifest'

export const STOP_LINE_CENTER_OFFSET_METERS = 0.2
export const CROSSWALK_STRIPE_COUNT = 8
export const CROSSWALK_FIRST_CENTER_METERS = 0.9
export const CROSSWALK_STRIPE_PITCH_METERS = 0.68

export interface LaneApproachSample {
  lane: RealisticLane
  point: Point2
}

export interface IntersectionApproachGeometry {
  tangent: Point2
  normal: Point2
  laneSamples: LaneApproachSample[]
  stopLineCenter: Point2
  halfWidth: number
  crosswalkCenters: Point2[]
}

export function pointAndTangent(lane: RealisticLane, distance: number, incoming: boolean) {
  const points = lane.points
  let index = incoming ? points.length - 1 : 0
  const step = incoming ? -1 : 1
  let remaining = distance
  let current = points[index]
  while (index + step >= 0 && index + step < points.length) {
    const next = points[index + step]
    const segmentLength = Math.hypot(next[0] - current[0], next[1] - current[1])
    if (segmentLength >= remaining) {
      const ratio = remaining / segmentLength
      const point: Point2 = [
        current[0] + (next[0] - current[0]) * ratio,
        current[1] + (next[1] - current[1]) * ratio,
      ]
      const tangent: Point2 = incoming
        ? [(current[0] - next[0]) / segmentLength, (current[1] - next[1]) / segmentLength]
        : [(next[0] - current[0]) / segmentLength, (next[1] - current[1]) / segmentLength]
      return { point, tangent }
    }
    remaining -= segmentLength
    current = next
    index += step
  }
  return { point: current, tangent: [1, 0] as Point2 }
}

export function buildIntersectionApproachGeometry(
  lanes: RealisticLane[],
  horizontalScale: number,
): IntersectionApproachGeometry | null {
  if (lanes.length === 0) return null
  const stopOffset = STOP_LINE_CENTER_OFFSET_METERS * horizontalScale
  const sampled = lanes.map((lane) => ({ lane, ...pointAndTangent(lane, stopOffset, true) }))
  const tangent = sampled[0].tangent
  const normal: Point2 = [-tangent[1], tangent[0]]
  const projected = sampled.map(({ point }) => point[0] * normal[0] + point[1] * normal[1])
  const min = Math.min(...projected) - lanes[0].width / 2
  const max = Math.max(...projected) + lanes[lanes.length - 1].width / 2
  const centerAlong = (min + max) / 2
  const center = sampled[Math.floor(sampled.length / 2)].point
  const centerProjection = center[0] * normal[0] + center[1] * normal[1]
  const stopLineCenter: Point2 = [
    center[0] + normal[0] * (centerAlong - centerProjection),
    center[1] + normal[1] * (centerAlong - centerProjection),
  ]
  const crosswalkCenters = Array.from({ length: CROSSWALK_STRIPE_COUNT }, (_, index) => {
    const offset = (CROSSWALK_FIRST_CENTER_METERS + index * CROSSWALK_STRIPE_PITCH_METERS)
      * horizontalScale
    return [
      stopLineCenter[0] + tangent[0] * offset,
      stopLineCenter[1] + tangent[1] * offset,
    ] as Point2
  })

  return {
    tangent,
    normal,
    laneSamples: sampled.map(({ lane, point }) => ({ lane, point })),
    stopLineCenter,
    halfWidth: (max - min) / 2,
    crosswalkCenters,
  }
}
