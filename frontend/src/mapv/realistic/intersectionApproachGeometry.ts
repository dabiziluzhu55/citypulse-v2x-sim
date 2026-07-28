import type { Point2, RealisticLane, RealisticRoadEdge } from './intersectionManifest'
import { visualLanePoints } from './intersectionRoadGeometry.ts'

export const STOP_LINE_CENTER_OFFSET_METERS = 0.8
export const CROSSWALK_STRIPE_COUNT = 5
export const CROSSWALK_FIRST_CENTER_METERS = 1.425
export const CROSSWALK_STRIPE_PITCH_METERS = 0.9
export const CROSSWALK_STRIPE_WIDTH_METERS = 0.45
export const SIGNAL_POLE_LATERAL_CLEARANCE_METERS = 1.05
export const SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS = 1.1

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
  crosswalkHalfWidth: number
  outerSide: -1 | 1
  outerBoundaryProjection: number
  crosswalkCenters: Point2[]
}

export function signalPoleBase(
  approach: IntersectionApproachGeometry,
  horizontalScale: number,
): Point2 {
  const {
    tangent,
    normal,
    stopLineCenter,
    outerSide,
    outerBoundaryProjection,
  } = approach
  const centerProjection = stopLineCenter[0] * normal[0] + stopLineCenter[1] * normal[1]
  return [
    stopLineCenter[0]
      + normal[0] * (
        outerBoundaryProjection - centerProjection
        + outerSide * SIGNAL_POLE_LATERAL_CLEARANCE_METERS * horizontalScale
      )
      - tangent[0] * SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS * horizontalScale,
    stopLineCenter[1]
      + normal[1] * (
        outerBoundaryProjection - centerProjection
        + outerSide * SIGNAL_POLE_LATERAL_CLEARANCE_METERS * horizontalScale
      )
      - tangent[1] * SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS * horizontalScale,
  ]
}

export function pointAndTangent(lane: RealisticLane, distance: number, incoming: boolean) {
  const points = visualLanePoints(lane)
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
  lanesOrEdge: RealisticLane[] | RealisticRoadEdge,
  horizontalScale: number,
  allEdges: RealisticRoadEdge[] = [],
): IntersectionApproachGeometry | null {
  const edge = Array.isArray(lanesOrEdge) ? null : lanesOrEdge
  const lanes = (Array.isArray(lanesOrEdge) ? lanesOrEdge : lanesOrEdge.lanes)
    .filter((lane) => lane.kind !== 'pedestrian' && lane.kind !== 'bicycle')
  if (lanes.length === 0) return null
  const stopOffset = STOP_LINE_CENTER_OFFSET_METERS * horizontalScale
  const sampled = lanes.map((lane) => ({ lane, ...pointAndTangent(lane, stopOffset, true) }))
  const tangent = sampled[0].tangent
  const normal: Point2 = [-tangent[1], tangent[0]]
  const projected = sampled.map(({ point }) => point[0] * normal[0] + point[1] * normal[1])
  const min = Math.min(...projected.map((value, index) => value - lanes[index].width / 2))
  const max = Math.max(...projected.map((value, index) => value + lanes[index].width / 2))
  const centerAlong = (min + max) / 2
  const center = sampled[Math.floor(sampled.length / 2)].point
  const centerProjection = center[0] * normal[0] + center[1] * normal[1]
  const stopLineCenter: Point2 = [
    center[0] + normal[0] * (centerAlong - centerProjection),
    center[1] + normal[1] * (centerAlong - centerProjection),
  ]
  let crossingMin = min
  let crossingMax = max
  if (edge) {
    for (const lane of edge.lanes.filter((value) => value.kind !== 'pedestrian' && !lanes.includes(value))) {
      const sample = pointAndTangent(lane, stopOffset, true)
      const projection = sample.point[0] * normal[0] + sample.point[1] * normal[1]
      crossingMin = Math.min(crossingMin, projection - lane.width / 2)
      crossingMax = Math.max(crossingMax, projection + lane.width / 2)
    }
    const endpoint = stopLineCenter
    let bestDistance = Number.POSITIVE_INFINITY
    let companion: RealisticRoadEdge | null = null
    for (const candidate of allEdges.filter((value) => !value.incoming && value.id !== edge.id)) {
      const candidateLanes = candidate.lanes.filter((lane) => lane.kind !== 'pedestrian')
      if (candidateLanes.length === 0) continue
      const candidateSample = pointAndTangent(candidateLanes[0], stopOffset, false)
      const headingDot = candidateSample.tangent[0] * tangent[0] + candidateSample.tangent[1] * tangent[1]
      const candidateDistance = Math.hypot(
        candidateSample.point[0] - endpoint[0],
        candidateSample.point[1] - endpoint[1],
      )
      if (headingDot > -0.82 || candidateDistance >= bestDistance || candidateDistance > 32 * horizontalScale) continue
      bestDistance = candidateDistance
      companion = candidate
    }
    if (companion) {
      for (const lane of companion.lanes.filter((value) => value.kind !== 'pedestrian')) {
        const sample = pointAndTangent(lane, stopOffset, false)
        const projection = sample.point[0] * normal[0] + sample.point[1] * normal[1]
        crossingMin = Math.min(crossingMin, projection - lane.width / 2)
        crossingMax = Math.max(crossingMax, projection + lane.width / 2)
      }
    }
  }
  const crossingCenterAlong = (crossingMin + crossingMax) / 2
  const crosswalkBase: Point2 = [
    stopLineCenter[0] + normal[0] * (crossingCenterAlong - centerAlong),
    stopLineCenter[1] + normal[1] * (crossingCenterAlong - centerAlong),
  ]
  const crosswalkCenters = Array.from({ length: CROSSWALK_STRIPE_COUNT }, (_, index) => {
    const offset = (CROSSWALK_FIRST_CENTER_METERS + index * CROSSWALK_STRIPE_PITCH_METERS)
      * horizontalScale
    return [
      crosswalkBase[0] + tangent[0] * offset,
      crosswalkBase[1] + tangent[1] * offset,
    ] as Point2
  })
  const incomingCenter = centerAlong
  const outerSide: -1 | 1 = incomingCenter >= crossingCenterAlong ? 1 : -1

  return {
    tangent,
    normal,
    laneSamples: sampled.map(({ lane, point }) => ({ lane, point })),
    stopLineCenter,
    halfWidth: (max - min) / 2,
    crosswalkHalfWidth: (crossingMax - crossingMin) / 2,
    outerSide,
    outerBoundaryProjection: outerSide > 0 ? crossingMax : crossingMin,
    crosswalkCenters,
  }
}
