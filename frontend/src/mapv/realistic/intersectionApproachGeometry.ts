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

export const STOP_LINE_WIDTH_METERS = 0.42
// The downstream edge of the painted line is the SUMO lane end.
export const STOP_LINE_CENTER_OFFSET_METERS = STOP_LINE_WIDTH_METERS / 2
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
  footprint: CrosswalkFootprint
}

export interface CrosswalkFootprint {
  polygon: [Point2, Point2, Point2, Point2]
  clipped: boolean
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

const CROSSWALK_CONFLICT_CLEARANCE_METERS = 0.25
const CROSSWALK_MIN_FRAGMENT_METERS = 0.65

function cross(left: Point2, right: Point2): number {
  return left[0] * right[1] - left[1] * right[0]
}

function closestSegmentParameters(
  leftStart: Point2,
  leftTangent: Point2,
  leftLength: number,
  rightStart: Point2,
  rightTangent: Point2,
  rightLength: number,
): { leftDistance: number; rightDistance: number; separation: number } {
  let best = { leftDistance: 0, rightDistance: 0, separation: Infinity }
  const candidates: Array<[number, number]> = []
  const directionCross = cross(leftTangent, rightTangent)
  if (Math.abs(directionCross) > 1e-9) {
    const delta: Point2 = [rightStart[0] - leftStart[0], rightStart[1] - leftStart[1]]
    candidates.push([
      cross(delta, rightTangent) / directionCross,
      cross(delta, leftTangent) / directionCross,
    ])
  }
  for (const leftDistance of [0, leftLength]) {
    const point: Point2 = [
      leftStart[0] + leftTangent[0] * leftDistance,
      leftStart[1] + leftTangent[1] * leftDistance,
    ]
    candidates.push([leftDistance, Math.max(0, Math.min(rightLength,
      (point[0] - rightStart[0]) * rightTangent[0]
      + (point[1] - rightStart[1]) * rightTangent[1],
    ))])
  }
  for (const rightDistance of [0, rightLength]) {
    const point: Point2 = [
      rightStart[0] + rightTangent[0] * rightDistance,
      rightStart[1] + rightTangent[1] * rightDistance,
    ]
    candidates.push([Math.max(0, Math.min(leftLength,
      (point[0] - leftStart[0]) * leftTangent[0]
      + (point[1] - leftStart[1]) * leftTangent[1],
    )), rightDistance])
  }
  for (const [rawLeftDistance, rawRightDistance] of candidates) {
    const leftDistance = Math.max(0, Math.min(leftLength, rawLeftDistance))
    const rightDistance = Math.max(0, Math.min(rightLength, rawRightDistance))
    const dx = leftStart[0] + leftTangent[0] * leftDistance
      - rightStart[0] - rightTangent[0] * rightDistance
    const dy = leftStart[1] + leftTangent[1] * leftDistance
      - rightStart[1] - rightTangent[1] * rightDistance
    const separation = Math.hypot(dx, dy)
    if (separation < best.separation) best = { leftDistance, rightDistance, separation }
  }
  return best
}

function barFootprint(
  bar: Pick<CrosswalkBar, 'center' | 'length' | 'width'>,
  tangent: Point2,
  normal: Point2,
  clipped = false,
): CrosswalkFootprint {
  const halfLength = bar.length / 2
  const halfWidth = bar.width / 2
  return {
    polygon: [
      [bar.center[0] - tangent[0] * halfLength - normal[0] * halfWidth, bar.center[1] - tangent[1] * halfLength - normal[1] * halfWidth],
      [bar.center[0] + tangent[0] * halfLength - normal[0] * halfWidth, bar.center[1] + tangent[1] * halfLength - normal[1] * halfWidth],
      [bar.center[0] + tangent[0] * halfLength + normal[0] * halfWidth, bar.center[1] + tangent[1] * halfLength + normal[1] * halfWidth],
      [bar.center[0] - tangent[0] * halfLength + normal[0] * halfWidth, bar.center[1] - tangent[1] * halfLength + normal[1] * halfWidth],
    ],
    clipped,
  }
}

function barStart(bar: CrosswalkBar, tangent: Point2): Point2 {
  return [
    bar.center[0] - tangent[0] * bar.length / 2,
    bar.center[1] - tangent[1] * bar.length / 2,
  ]
}

function trimBarAtDistance(
  bar: CrosswalkBar,
  tangent: Point2,
  normal: Point2,
  distance: number,
  minimumLength: number,
): void {
  const start = barStart(bar, tangent)
  const nextLength = Math.max(0, Math.min(bar.length, distance))
  bar.length = nextLength >= minimumLength ? nextLength : 0
  bar.center = [
    start[0] + tangent[0] * bar.length / 2,
    start[1] + tangent[1] * bar.length / 2,
  ]
  bar.footprint = barFootprint(bar, tangent, normal, true)
}

function trimCrosswalkConflicts(
  approaches: IntersectionApproachGeometry[],
  horizontalScale: number,
): void {
  const clearance = CROSSWALK_CONFLICT_CLEARANCE_METERS * horizontalScale
  const minimumLength = CROSSWALK_MIN_FRAGMENT_METERS * horizontalScale
  for (let leftIndex = 0; leftIndex < approaches.length; leftIndex += 1) {
    const left = approaches[leftIndex]
    for (let rightIndex = leftIndex + 1; rightIndex < approaches.length; rightIndex += 1) {
      const right = approaches[rightIndex]
      const directionCross = cross(left.tangent, right.tangent)
      const angleSine = Math.abs(directionCross)
      if (angleSine < 0.18) continue
      for (const leftBar of left.crosswalkBars) {
        if (leftBar.length <= 0) continue
        for (const rightBar of right.crosswalkBars) {
          if (rightBar.length <= 0) continue
          const leftStart = barStart(leftBar, left.tangent)
          const rightStart = barStart(rightBar, right.tangent)
          const closest = closestSegmentParameters(
            leftStart,
            left.tangent,
            leftBar.length,
            rightStart,
            right.tangent,
            rightBar.length,
          )
          if (closest.separation >= (leftBar.width + rightBar.width) / 2 + clearance) continue
          const leftAllowance = (rightBar.width / 2 + clearance / 2) / angleSine
          const rightAllowance = (leftBar.width / 2 + clearance / 2) / angleSine
          trimBarAtDistance(
            leftBar,
            left.tangent,
            left.normal,
            closest.leftDistance - leftAllowance,
            minimumLength,
          )
          trimBarAtDistance(
            rightBar,
            right.tangent,
            right.normal,
            closest.rightDistance - rightAllowance,
            minimumLength,
          )
        }
      }
    }
  }
  for (const approach of approaches) {
    approach.crosswalkBars = approach.crosswalkBars.filter((bar) => bar.length >= minimumLength)
    approach.crosswalkCenters = approach.crosswalkBars.map((bar) => bar.center)
  }
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

function controlBoundaryPointAndTangent(
  lane: RealisticLane,
  distance: number,
  incoming: boolean,
) {
  const points = lane.vehicleGuidePoints?.length
    ? lane.vehicleGuidePoints
    : visualLanePoints(lane)
  return pointAndTangentOnPolyline(points, distance, incoming)
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
  _setbackMeters = 0,
): IntersectionApproachGeometry | null {
  const edge = Array.isArray(lanesOrEdge) ? null : lanesOrEdge
  const lanes = (Array.isArray(lanesOrEdge) ? lanesOrEdge : lanesOrEdge.lanes)
    .filter((lane) => lane.kind !== 'pedestrian' && lane.kind !== 'bicycle')
  if (lanes.length === 0) return null
  const stopOffset = STOP_LINE_CENTER_OFFSET_METERS * horizontalScale
  const sampled = lanes.map((lane) => ({
    lane,
    ...controlBoundaryPointAndTangent(lane, stopOffset, true),
  }))
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
      const sample = controlBoundaryPointAndTangent(lane, stopOffset, true)
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
    crosswalkBase[0]
      + tangent[0] * CROSSWALK_FIRST_CENTER_METERS * horizontalScale,
    crosswalkBase[1]
      + tangent[1] * CROSSWALK_FIRST_CENTER_METERS * horizontalScale,
  ]
  const barWidth = CROSSWALK_STRIPE_WIDTH_METERS * horizontalScale
  const projections = buildCrosswalkBarProjections(crossingMin, crossingMax, horizontalScale)
  const crosswalkBars = projections.map((projection): CrosswalkBar => {
    const lateralOffset = projection - crossingCenterAlong
    const bar = {
      center: [
        crossingCenter[0] + normal[0] * lateralOffset,
        crossingCenter[1] + normal[1] * lateralOffset,
      ] as Point2,
      length: CROSSWALK_DEPTH_METERS * horizontalScale,
      width: barWidth,
    }
    return { ...bar, footprint: barFootprint(bar, tangent, normal) }
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
    setbackMeters: 0,
  }
}

export function crosswalksOverlap(
  left: IntersectionApproachGeometry,
  right: IntersectionApproachGeometry,
  _horizontalScale: number,
): boolean {
  return left.crosswalkBars.some((leftBar) => right.crosswalkBars.some((rightBar) => {
    const delta: Point2 = [
      rightBar.center[0] - leftBar.center[0],
      rightBar.center[1] - leftBar.center[1],
    ]
    return [left.tangent, left.normal, right.tangent, right.normal].every((axis) => {
      const separation = Math.abs(delta[0] * axis[0] + delta[1] * axis[1])
      const leftRadius = Math.abs(left.tangent[0] * axis[0] + left.tangent[1] * axis[1]) * leftBar.length / 2
        + Math.abs(left.normal[0] * axis[0] + left.normal[1] * axis[1]) * leftBar.width / 2
      const rightRadius = Math.abs(right.tangent[0] * axis[0] + right.tangent[1] * axis[1]) * rightBar.length / 2
        + Math.abs(right.normal[0] * axis[0] + right.normal[1] * axis[1]) * rightBar.width / 2
      return separation < leftRadius + rightRadius - 1e-6
    })
  }))
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
  trimCrosswalkConflicts(initialGeometry, horizontalScale)

  return incoming.flatMap((edge, index) => {
    const geometry = initialGeometry[index]
    return geometry ? [{ edge, geometry }] : []
  })
}
