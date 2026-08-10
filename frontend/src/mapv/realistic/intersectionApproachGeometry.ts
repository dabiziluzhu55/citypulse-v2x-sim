import type { Point2, RealisticLane, RealisticRoadEdge } from './intersectionManifest'
import {
  buildCrosswalkBarProjections,
  CROSSWALK_CENTER_OFFSET_METERS,
  CROSSWALK_DEPTH_METERS,
  CROSSWALK_EDGE_INSET_METERS,
  CROSSWALK_SETBACK_METERS,
  CROSSWALK_STRIPE_GAP_METERS,
  CROSSWALK_STRIPE_WIDTH_METERS,
} from '../crosswalkGeometry.ts'
import {
  edgeCenterline,
  visualLanePoints,
} from './intersectionRoadGeometry.ts'

export const STOP_LINE_CENTER_OFFSET_METERS = 0.8
export {
  CROSSWALK_DEPTH_METERS,
  CROSSWALK_EDGE_INSET_METERS,
  CROSSWALK_SETBACK_METERS,
  CROSSWALK_STRIPE_GAP_METERS,
  CROSSWALK_STRIPE_WIDTH_METERS,
}
export const CROSSWALK_FIRST_CENTER_METERS = CROSSWALK_CENTER_OFFSET_METERS
export const SIGNAL_POLE_LATERAL_CLEARANCE_METERS = 1.05
export const SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS = 1.1

export interface LaneApproachSample {
  lane: RealisticLane
  point: Point2
}

export interface CrosswalkBar {
  center: Point2
  length: number
  width: number
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
  crosswalkBars: CrosswalkBar[]
  setbackMeters: number
}

export interface PositionedIntersectionApproach {
  edge: RealisticRoadEdge
  geometry: IntersectionApproachGeometry
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

function pointAndTangentOnPolyline(points: Point2[], distance: number, incoming: boolean) {
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
  const fallbackIndex = Math.max(0, Math.min(points.length - 2, incoming ? points.length - 2 : 0))
  const fallbackStart = points[fallbackIndex]
  const fallbackEnd = points[fallbackIndex + 1]
  const fallbackLength = Math.hypot(
    fallbackEnd[0] - fallbackStart[0],
    fallbackEnd[1] - fallbackStart[1],
  ) || 1
  return {
    point: current,
    tangent: [
      (fallbackEnd[0] - fallbackStart[0]) / fallbackLength,
      (fallbackEnd[1] - fallbackStart[1]) / fallbackLength,
    ] as Point2,
  }
}

export function pointAndTangent(lane: RealisticLane, distance: number, incoming: boolean) {
  return pointAndTangentOnPolyline(visualLanePoints(lane), distance, incoming)
}

function referenceTangent(
  edge: RealisticRoadEdge | null,
  sampled: Array<{ tangent: Point2 }>,
  distance: number,
  incoming: boolean,
): Point2 {
  if (edge) return pointAndTangentOnPolyline(edgeCenterline(edge), distance, incoming).tangent
  const sum = sampled.reduce((value, sample) => (
    [value[0] + sample.tangent[0], value[1] + sample.tangent[1]] as Point2
  ), [0, 0] as Point2)
  const magnitude = Math.hypot(sum[0], sum[1]) || 1
  return [sum[0] / magnitude, sum[1] / magnitude]
}

function companionForApproach(
  edge: RealisticRoadEdge,
  allEdges: RealisticRoadEdge[],
  tangent: Point2,
  stopLineCenter: Point2,
  stopOffset: number,
  horizontalScale: number,
): RealisticRoadEdge | null {
  const normal: Point2 = [-tangent[1], tangent[0]]
  let best: { edge: RealisticRoadEdge; score: number } | null = null
  for (const candidate of allEdges.filter((value) => !value.incoming && value.id !== edge.id)) {
    if (!candidate.lanes.some((lane) => lane.kind !== 'pedestrian')) continue
    const sample = pointAndTangentOnPolyline(edgeCenterline(candidate), stopOffset, false)
    const headingDot = sample.tangent[0] * tangent[0] + sample.tangent[1] * tangent[1]
    if (headingDot > -0.82) continue
    const dx = sample.point[0] - stopLineCenter[0]
    const dy = sample.point[1] - stopLineCenter[1]
    const longitudinal = Math.abs(dx * tangent[0] + dy * tangent[1])
    const lateral = Math.abs(dx * normal[0] + dy * normal[1])
    if (longitudinal > 12 * horizontalScale || lateral > 32 * horizontalScale) continue
    const score = longitudinal * 2 + lateral + (headingDot + 1) * 8 * horizontalScale
    if (!best || score < best.score) best = { edge: candidate, score }
  }
  return best?.edge ?? null
}

export function buildIntersectionApproachGeometry(
  lanesOrEdge: RealisticLane[] | RealisticRoadEdge,
  horizontalScale: number,
  allEdges: RealisticRoadEdge[] = [],
  setbackMeters = 0,
): IntersectionApproachGeometry | null {
  const edge = Array.isArray(lanesOrEdge) ? null : lanesOrEdge
  const lanes = (Array.isArray(lanesOrEdge) ? lanesOrEdge : lanesOrEdge.lanes)
    .filter((lane) => lane.kind !== 'pedestrian' && lane.kind !== 'bicycle')
  if (lanes.length === 0) return null
  const stopOffset = (STOP_LINE_CENTER_OFFSET_METERS + setbackMeters) * horizontalScale
  const sampled = lanes.map((lane) => ({ lane, ...pointAndTangent(lane, stopOffset, true) }))
  const tangent = referenceTangent(edge, sampled, stopOffset, true)
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
    const companion = companionForApproach(
      edge,
      allEdges,
      tangent,
      stopLineCenter,
      stopOffset,
      horizontalScale,
    )
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
  const crossingCenter: Point2 = [
    crosswalkBase[0] + tangent[0] * CROSSWALK_FIRST_CENTER_METERS * horizontalScale,
    crosswalkBase[1] + tangent[1] * CROSSWALK_FIRST_CENTER_METERS * horizontalScale,
  ]
  const barWidth = CROSSWALK_STRIPE_WIDTH_METERS * horizontalScale
  const projections = buildCrosswalkBarProjections(crossingMin, crossingMax, horizontalScale)
  const crosswalkBars = projections.map((projection): CrosswalkBar => {
    const lateralOffset = projection - crossingCenterAlong
    return {
      center: [
        crossingCenter[0] + normal[0] * lateralOffset,
        crossingCenter[1] + normal[1] * lateralOffset,
      ],
      length: CROSSWALK_DEPTH_METERS * horizontalScale,
      width: barWidth,
    }
  })
  const crosswalkCenters = crosswalkBars.map((bar) => bar.center)
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
    crosswalkBars,
    setbackMeters,
  }
}

function crosswalkCenter(approach: IntersectionApproachGeometry): Point2 {
  const centers = approach.crosswalkCenters
  return centers[Math.floor(centers.length / 2)] ?? approach.stopLineCenter
}

function projectionRadius(
  approach: IntersectionApproachGeometry,
  axis: Point2,
  horizontalScale: number,
): number {
  const tangentProjection = Math.abs(approach.tangent[0] * axis[0] + approach.tangent[1] * axis[1])
  const normalProjection = Math.abs(approach.normal[0] * axis[0] + approach.normal[1] * axis[1])
  return tangentProjection * CROSSWALK_DEPTH_METERS * horizontalScale / 2
    + normalProjection * approach.crosswalkHalfWidth
}

export function crosswalksOverlap(
  left: IntersectionApproachGeometry,
  right: IntersectionApproachGeometry,
  horizontalScale: number,
): boolean {
  const leftCenter = crosswalkCenter(left)
  const rightCenter = crosswalkCenter(right)
  const delta: Point2 = [rightCenter[0] - leftCenter[0], rightCenter[1] - leftCenter[1]]
  const axes = [left.tangent, left.normal, right.tangent, right.normal]
  const clearance = 0.65 * horizontalScale
  return axes.every((axis) => {
    const separation = Math.abs(delta[0] * axis[0] + delta[1] * axis[1])
    return separation <= projectionRadius(left, axis, horizontalScale)
      + projectionRadius(right, axis, horizontalScale)
      + clearance
  })
}

export function buildCollisionFreeIntersectionApproaches(
  edges: RealisticRoadEdge[],
  horizontalScale: number,
): PositionedIntersectionApproach[] {
  const incomingCandidates = edges
    .filter((edge) => edge.incoming && edge.incident !== false)
    .sort((left, right) => left.id.localeCompare(right.id))
  const incoming: RealisticRoadEdge[] = []
  const initialGeometry: IntersectionApproachGeometry[] = []
  for (const edge of incomingCandidates) {
    const geometry = buildIntersectionApproachGeometry(edge, horizontalScale, edges)
    if (!geometry) continue
    const duplicateIndex = initialGeometry.findIndex((candidate) => {
      const headingDot = candidate.tangent[0] * geometry.tangent[0]
        + candidate.tangent[1] * geometry.tangent[1]
      return headingDot > 0.96
        && Math.hypot(
          candidate.stopLineCenter[0] - geometry.stopLineCenter[0],
          candidate.stopLineCenter[1] - geometry.stopLineCenter[1],
        ) < 12 * horizontalScale
    })
    if (duplicateIndex < 0) {
      incoming.push(edge)
      initialGeometry.push(geometry)
    } else if (edge.lanes.length > incoming[duplicateIndex].lanes.length) {
      incoming[duplicateIndex] = edge
      initialGeometry[duplicateIndex] = geometry
    }
  }
  const setbacks = incoming.map(() => 0)
  let approaches: Array<IntersectionApproachGeometry | null> = initialGeometry

  for (let iteration = 0; iteration < 24; iteration += 1) {
    const colliding = new Set<number>()
    for (let left = 0; left < approaches.length; left += 1) {
      if (!approaches[left]) continue
      for (let right = left + 1; right < approaches.length; right += 1) {
        if (!approaches[right]) continue
        if (crosswalksOverlap(approaches[left]!, approaches[right]!, horizontalScale)) {
          colliding.add(left)
          colliding.add(right)
        }
      }
    }
    if (colliding.size === 0) break
    for (const index of colliding) setbacks[index] += 1.5
    approaches = incoming.map((edge, index) => buildIntersectionApproachGeometry(
      edge,
      horizontalScale,
      edges,
      setbacks[index],
    ))
  }

  return incoming.flatMap((edge, index) => {
    const geometry = approaches[index]
    return geometry ? [{ edge, geometry }] : []
  })
}
