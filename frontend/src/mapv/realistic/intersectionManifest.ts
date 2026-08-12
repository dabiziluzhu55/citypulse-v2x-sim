import {
  sumoHeadingTransformIsValid,
  type SumoHeadingTransform,
} from '../sumoHeadingTransform.ts'

export type Point2 = [number, number]

export type RealisticLaneKind = 'driving' | 'bicycle' | 'pedestrian'

export interface RealisticLane {
  id: string
  index: number
  kind?: RealisticLaneKind
  width: number
  widthMeters?: number
  speed: number
  points: Point2[]
  renderPoints?: Point2[]
  vehicleGuidePoints?: Point2[]
  vehicleGuideSourceStationsMeters?: number[]
  vehicleGuideSourceStartMeters?: number
  vehicleGuideSourceEndMeters?: number
}

export interface RealisticRoadEdge {
  id: string
  incoming: boolean
  incident?: boolean
  centerline?: Point2[]
  roadWidth?: number
  surfaceExclusions?: RealisticRoadSurfaceExclusion[]
  lanes: RealisticLane[]
}

export interface RealisticRoadSurfaceExclusion {
  startOffsetMeters: number
  endOffsetMeters: number
  reason:
    | 'building_overlap'
    | 'building_overlap_far_field'
    | 'building_overlap_visual_override'
  source: 'building_triangle_audit' | 'manual_visual_review'
}

export interface RoadSurfacePolygon {
  outer: Point2[]
  holes?: Point2[][]
}

export interface RealisticRoadJoint {
  jointId: string
  junctionId: string
  kind: 'continuation' | 'junction'
  connectedEdgeIds: string[]
  maxGapMeters: number
  overlapMeters: number
  source: 'sumo_topology' | 'sumo_junction_shape'
  surfaceHidden?: {
    reason: 'building_overlap_visual_override'
    source: 'manual_visual_review'
  }
  polygons: {
    sidewalk: Point2[]
    curb: Point2[]
    asphalt: Point2[]
  }
  surfaceParts?: {
    sidewalk: RoadSurfacePolygon[]
    curb: RoadSurfacePolygon[]
    asphalt: RoadSurfacePolygon[]
  }
}

export interface RealisticConnection {
  tlsId: string
  linkIndex: number
  fromEdge: string
  fromLane: number
  toEdge: string
  toLane: number
  direction: 's' | 'l' | 'r' | 't'
  directionLabel: string
  viaLaneId?: string
  viaPoints?: Point2[]
  renderPoints?: Point2[]
  vehicleGuidePoints?: Point2[]
  vehicleGuideSourceStationsMeters?: number[]
  vehicleSourcePoints?: Point2[]
  vehicleGuideEndpointLimited?: boolean
  viaSegments?: Array<{
    laneId: string
    points: Point2[]
    renderPoints: Point2[]
    vehicleGuidePoints?: Point2[]
    vehicleGuideSourceStationsMeters?: number[]
    vehicleSourcePoints?: Point2[]
  }>
}

export interface RealisticPhase {
  tlsId?: string
  index: number
  durationSeconds: number
  state: string
  label: string
}

export interface RealisticSignalStageTemplate {
  green: string
  yellow: string
  clearance: string
  [stage: string]: string
}

export type RealisticPhaseTemplates = Record<string, Record<string, RealisticSignalStageTemplate>>

export interface RealisticIntersectionManifest {
  schemaVersion: 1 | 2 | 3
  intersectionId: string
  junctionId?: string
  tlsId?: string
  tlsIds?: string[]
  radiusMeters: number
  radiusSceneUnits?: number
  horizontalScale?: number
  sumoUnitScale?: number
  sumoHeadingTransform?: SumoHeadingTransform
  renderCoordinateSystem?: string
  origin: {
    x: number
    y: number
    longitude: number
    latitude: number
    bd09?: Point2
    webMercator?: Point2
  }
  junctionShape: Point2[]
  edges: RealisticRoadEdge[]
  roadJoints?: RealisticRoadJoint[]
  connections: RealisticConnection[]
  vehicleConnections?: RealisticConnection[]
  vehicleConnectionSourceSha256?: string
  phases: RealisticPhase[]
  phaseTemplates?: RealisticPhaseTemplates
  signalGroups: Array<{ tlsId?: string; laneId: string; linkIndexes: number[] }>
}

export type SignalColor = 'red' | 'amber' | 'green'

export function signalColorForState(state: string, linkIndex: number): SignalColor {
  const value = state[linkIndex]?.toLowerCase()
  if (value === 'g') return 'green'
  if (value === 'y') return 'amber'
  return 'red'
}

export async function loadIntersectionManifest(
  url = '/intersections/v3/demo_2/manifest.json',
): Promise<RealisticIntersectionManifest> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Intersection asset returned HTTP ${response.status}`)
  const value = await response.json() as RealisticIntersectionManifest
  if (![1, 2, 3].includes(value.schemaVersion) || !value.connections.length || !value.edges.length) {
    throw new Error('Intersection asset structure is incomplete')
  }
  if (url.includes('/intersections/v3/') && (
    value.schemaVersion !== 3
    || value.renderCoordinateSystem !== 'LOCAL_BD09_WEB_MERCATOR_METERS, Z-up'
    || !sumoHeadingTransformIsValid(value.sumoHeadingTransform)
  )) {
    throw new Error('Intersection asset coordinate contract is incompatible')
  }
  for (const joint of value.roadJoints ?? []) {
    if (
      !joint.jointId
      || !joint.junctionId
      || !['continuation', 'junction'].includes(joint.kind)
      || joint.connectedEdgeIds.length < 2
      || !Object.values(joint.polygons).every((points) => Array.isArray(points) && points.length >= 3)
      || (joint.surfaceParts && !Object.values(joint.surfaceParts).every((parts) => (
        Array.isArray(parts)
        && parts.length > 0
        && parts.every((part) => (
          Array.isArray(part.outer)
          && part.outer.length >= 3
          && (part.holes ?? []).every((hole) => Array.isArray(hole) && hole.length >= 3)
        ))
      )))
      || (joint.surfaceHidden && (
        joint.surfaceHidden.reason !== 'building_overlap_visual_override'
        || joint.surfaceHidden.source !== 'manual_visual_review'
      ))
    ) {
      throw new Error(`Intersection road joint ${joint.jointId || '<unknown>'} is invalid`)
    }
  }
  for (const edge of value.edges) {
    for (const exclusion of edge.surfaceExclusions ?? []) {
      if (
        !Number.isFinite(exclusion.startOffsetMeters)
        || !Number.isFinite(exclusion.endOffsetMeters)
        || exclusion.startOffsetMeters < 0
        || exclusion.endOffsetMeters <= exclusion.startOffsetMeters
        || ![
          'building_overlap',
          'building_overlap_far_field',
          'building_overlap_visual_override',
        ].includes(exclusion.reason)
        || !['building_triangle_audit', 'manual_visual_review'].includes(exclusion.source)
        || (
          exclusion.reason === 'building_overlap_visual_override'
          && exclusion.source !== 'manual_visual_review'
        )
      ) {
        throw new Error(`Intersection road surface exclusion ${edge.id} is invalid`)
      }
    }
  }
  return value
}

export function realisticIntersectionAssetUrl(intersectionId: string): string {
  if (!/^demo_(?:[1-9]|1\d|20)$/.test(intersectionId)) {
    throw new Error(`Unsupported intersection id: ${intersectionId}`)
  }
  return `/intersections/v3/${intersectionId}/manifest.json`
}
