import * as THREE from 'three'
import { pointAndTangent } from './intersectionApproachGeometry.ts'
import type {
  Point2,
  RealisticConnection,
  RealisticIntersectionManifest,
  RealisticLane,
  RealisticLaneKind,
} from './intersectionManifest'
import { visualLanePoints } from './intersectionRoadGeometry.ts'

export type LaneMovement = RealisticConnection['direction']
export type LaneArrowPattern = 'l' | 's' | 'r' | 'l+s' | 's+r' | 'l+r' | 'l+s+r' | 't'

export interface LaneDirectionArrow {
  key: string
  edgeId: string
  laneIndex: number
  pattern: LaneArrowPattern
  movements: LaneMovement[]
  point: Point2
  tangent: Point2
  headingRadians: number
  sampleDistanceMeters: number
  laneWidth: number
  laneKind: RealisticLaneKind | undefined
  scale: number
}

export interface LaneArrowAudit {
  controlledLaneCount: number
  multiMovementLaneCount: number
  unsupported: string[]
  warnings: string[]
  arrows: LaneDirectionArrow[]
}

export const LANE_ARROW_SAMPLE_DISTANCE_METERS = 24
export const LANE_ARROW_SURFACE_Z = 0.085
export const LANE_ARROW_RENDER_ORDER = 33
export const LANE_ARROW_MAX_LANE_WIDTH_RATIO = 0.8
export const LANE_ARROW_MAX_VISIBLE_RANGE_METERS = 6_000

const MOVEMENT_ORDER: LaneMovement[] = ['l', 's', 'r', 't']
const SUPPORTED_PATTERNS = new Set<LaneArrowPattern>([
  'l',
  's',
  'r',
  'l+s',
  's+r',
  'l+r',
  'l+s+r',
  't',
])

const TEMPLATE_WIDTH: Record<LaneArrowPattern, number> = {
  l: 1.93,
  s: 1.44,
  r: 1.93,
  'l+s': 2.47,
  's+r': 2.47,
  'l+r': 3.5,
  'l+s+r': 3.5,
  t: 2.06,
}

function laneLength(points: Point2[]): number {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
}

function laneKey(edgeId: string, laneIndex: number): string {
  return `${edgeId}:${laneIndex}`
}

function canonicalMovements(connections: RealisticConnection[]): LaneMovement[] {
  const values = new Set(connections.map((connection) => connection.direction))
  return MOVEMENT_ORDER.filter((movement) => values.has(movement))
}

export function laneArrowPattern(movements: LaneMovement[]): LaneArrowPattern | null {
  const unique = MOVEMENT_ORDER.filter((movement) => movements.includes(movement))
  if (unique.includes('t') && unique.length !== 1) return null
  const pattern = unique.join('+') as LaneArrowPattern
  return SUPPORTED_PATTERNS.has(pattern) ? pattern : null
}

export function aggregateLaneMovements(
  connections: RealisticConnection[],
): Map<string, { edgeId: string; laneIndex: number; movements: LaneMovement[] }> {
  const grouped = new Map<string, RealisticConnection[]>()
  for (const connection of connections) {
    const key = laneKey(connection.fromEdge, connection.fromLane)
    grouped.set(key, [...(grouped.get(key) ?? []), connection])
  }
  return new Map([...grouped].map(([key, values]) => [key, {
    edgeId: values[0].fromEdge,
    laneIndex: values[0].fromLane,
    movements: canonicalMovements(values),
  }]))
}

function arrowForLane(
  edgeId: string,
  lane: RealisticLane,
  movements: LaneMovement[],
  horizontalScale: number,
): LaneDirectionArrow | null {
  const pattern = laneArrowPattern(movements)
  if (!pattern) return null
  const points = visualLanePoints(lane)
  const length = laneLength(points)
  const targetDistance = LANE_ARROW_SAMPLE_DISTANCE_METERS * horizontalScale
  const minimumDistance = Math.min(length * 0.5, 3 * horizontalScale)
  const sampleDistance = Math.min(targetDistance, Math.max(minimumDistance, length * 0.42))
  const sample = pointAndTangent(lane, sampleDistance, true)
  const maximumScale = lane.width * LANE_ARROW_MAX_LANE_WIDTH_RATIO / TEMPLATE_WIDTH[pattern]
  return {
    key: laneKey(edgeId, lane.index),
    edgeId,
    laneIndex: lane.index,
    pattern,
    movements,
    point: sample.point,
    tangent: sample.tangent,
    headingRadians: Math.atan2(sample.tangent[1], sample.tangent[0]) - Math.PI / 2,
    sampleDistanceMeters: sampleDistance / horizontalScale,
    laneWidth: lane.width,
    laneKind: lane.kind,
    scale: Math.min(horizontalScale, maximumScale),
  }
}

export function auditLaneDirectionArrows(
  manifest: RealisticIntersectionManifest,
): LaneArrowAudit {
  const horizontalScale = manifest.horizontalScale ?? 1
  const movementsByLane = aggregateLaneMovements(manifest.connections)
  const edgesById = new Map(manifest.edges.map((edge) => [edge.id, edge]))
  const arrows: LaneDirectionArrow[] = []
  const unsupported: string[] = []
  const warnings: string[] = []
  let multiMovementLaneCount = 0

  for (const [key, controlled] of movementsByLane) {
    if (controlled.movements.length > 1) multiMovementLaneCount += 1
    const edge = edgesById.get(controlled.edgeId)
    const lane = edge?.lanes.find((candidate) => candidate.index === controlled.laneIndex)
    if (!lane || lane.kind === 'pedestrian') {
      unsupported.push(`${key}: controlled driving lane is missing`)
      continue
    }
    if (lane.kind === 'bicycle') {
      warnings.push(`${key}: SUMO-controlled lane is classified as bicycle`)
    }
    const arrow = arrowForLane(
      controlled.edgeId,
      lane,
      controlled.movements,
      horizontalScale,
    )
    if (!arrow) {
      unsupported.push(`${key}: unsupported movements ${controlled.movements.join('+')}`)
      continue
    }
    arrows.push(arrow)
  }

  return {
    controlledLaneCount: movementsByLane.size,
    multiMovementLaneCount,
    unsupported,
    warnings,
    arrows,
  }
}

export function buildLaneDirectionArrows(
  manifest: RealisticIntersectionManifest,
): LaneDirectionArrow[] {
  const audit = auditLaneDirectionArrows(manifest)
  if (audit.unsupported.length > 0) {
    throw new Error(`Unsupported lane arrows in ${manifest.intersectionId}: ${audit.unsupported.join('; ')}`)
  }
  if (audit.arrows.length !== audit.controlledLaneCount) {
    throw new Error(`Lane arrow count mismatch in ${manifest.intersectionId}`)
  }
  return audit.arrows
}

function polygon(points: Point2[]): THREE.Shape {
  const shape = new THREE.Shape()
  shape.moveTo(...points[0])
  points.slice(1).forEach(([x, y]) => shape.lineTo(x, y))
  shape.closePath()
  return shape
}

function mirrored(points: Point2[]): Point2[] {
  return points.map(([x, y]) => [-x, y] as Point2).reverse()
}

const STEM: Point2[] = [[-0.18, -2.4], [0.18, -2.4], [0.18, 0.82], [-0.18, 0.82]]
const STRAIGHT_HEAD: Point2[] = [[-0.18, 0.55], [0.18, 0.55], [0.18, 1.18], [0.72, 1.18], [0, 2.4], [-0.72, 1.18], [-0.18, 1.18]]
const LEFT_HEAD: Point2[] = [[0.08, 0.48], [-0.82, 0.48], [-0.82, -0.08], [-1.75, 0.72], [-0.82, 1.52], [-0.82, 0.96], [0.08, 0.96]]
const RIGHT_HEAD = mirrored(LEFT_HEAD)
const UTURN_HEAD: Point2[] = [
  [0.18, 0.38], [0.18, 0.92], [-0.02, 1.28], [-0.42, 1.5], [-0.86, 1.5],
  [-1.18, 1.3], [-1.34, 0.96], [-1.34, 0.62], [-0.82, 0.62], [-1.52, -0.22],
  [-1.88, 0.82], [-1.42, 0.62], [-0.78, 0.62], [-0.78, 0.87], [-0.68, 1.0],
  [-0.48, 1.06], [-0.28, 1.0], [-0.18, 0.8], [-0.18, 0.38],
]

export function createLaneArrowGeometry(pattern: LaneArrowPattern): THREE.ShapeGeometry {
  const shapes = [polygon(STEM)]
  if (pattern.includes('l')) shapes.push(polygon(LEFT_HEAD))
  if (pattern.includes('s')) shapes.push(polygon(STRAIGHT_HEAD))
  if (pattern.includes('r')) shapes.push(polygon(RIGHT_HEAD))
  if (pattern === 't') shapes.push(polygon(UTURN_HEAD))
  const geometry = new THREE.ShapeGeometry(shapes)
  geometry.userData.pattern = pattern
  geometry.computeBoundingBox()
  return geometry
}

export function createLaneArrowMaterial(): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color: 0xffffff,
    side: THREE.DoubleSide,
    depthTest: true,
    depthWrite: false,
    toneMapped: false,
    polygonOffset: true,
    polygonOffsetFactor: -8,
    polygonOffsetUnits: -8,
  })
}

export function laneArrowsAvailableForLod(lod: 'overview' | 'medium' | 'full'): boolean {
  return lod !== 'overview'
}
