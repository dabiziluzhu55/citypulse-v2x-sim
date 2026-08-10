import type {
  Point2,
  RealisticRoadEdge,
  RealisticRoadJoint,
} from './intersectionManifest.ts'
import {
  convexHull,
  edgeCenterline,
  edgeRoadWidth,
  expandPolygon,
  samplePolyline,
} from './intersectionRoadGeometry.ts'

export interface RoadEndpointTopology {
  edgeId: string
  junctionId: string
  endpoint: 'start' | 'end'
}

export interface RoadConnectionTopology {
  junctionId: string
  fromEdge: string
  toEdge: string
}

export interface AuthoritativeRoadJunction {
  junctionId: string
  shape: Point2[]
  internalLaneIds: string[]
}

export interface RoadJointBuildInput {
  edges: RealisticRoadEdge[]
  endpoints: RoadEndpointTopology[]
  connections: RoadConnectionTopology[]
  authoritativeJunctions?: AuthoritativeRoadJunction[]
  primaryJunctionId: string
  primaryJunctionShape: Point2[]
  horizontalScale: number
  maximumSecondaryGapMeters?: number
  overlapMeters?: number
}

interface EndpointGeometry extends RoadEndpointTopology {
  sourceCenter: Point2
  center: Point2
  cap: Point2[]
}

const DEFAULT_MAXIMUM_SECONDARY_GAP_METERS = 20
const DEFAULT_OVERLAP_METERS = 0.5

function distance(a: Point2, b: Point2): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1])
}

function normalAtEndpoint(points: Point2[], endpoint: 'start' | 'end'): Point2 {
  const from = endpoint === 'start' ? points[0] : points.at(-2)!
  const to = endpoint === 'start' ? points[1] : points.at(-1)!
  const dx = to[0] - from[0]
  const dy = to[1] - from[1]
  const magnitude = Math.hypot(dx, dy) || 1
  return [-dy / magnitude, dx / magnitude]
}

function endpointGeometry(
  topology: RoadEndpointTopology,
  edge: RealisticRoadEdge,
  overlapSceneUnits: number,
): EndpointGeometry | null {
  const points = edgeCenterline(edge)
  if (points.length < 2) return null
  const total = points.slice(1).reduce((sum, point, index) => sum + distance(points[index], point), 0)
  if (total <= 1e-6) return null
  const overlapProgress = Math.min(0.45, overlapSceneUnits / total)
  const sourceCenter = topology.endpoint === 'start' ? points[0] : points.at(-1)!
  const center = samplePolyline(points, topology.endpoint === 'start' ? overlapProgress : 1 - overlapProgress)
  const normal = normalAtEndpoint(points, topology.endpoint)
  const halfWidth = edgeRoadWidth(edge) / 2
  return {
    ...topology,
    sourceCenter,
    center,
    cap: [
      [sourceCenter[0] + normal[0] * halfWidth, sourceCenter[1] + normal[1] * halfWidth],
      [sourceCenter[0] - normal[0] * halfWidth, sourceCenter[1] - normal[1] * halfWidth],
      [center[0] + normal[0] * halfWidth, center[1] + normal[1] * halfWidth],
      [center[0] - normal[0] * halfWidth, center[1] - normal[1] * halfWidth],
    ],
  }
}

function polygonArea(points: Point2[]): number {
  return Math.abs(points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]
    return sum + point[0] * next[1] - next[0] * point[1]
  }, 0)) / 2
}

function joint(
  jointId: string,
  junctionId: string,
  kind: RealisticRoadJoint['kind'],
  endpoints: EndpointGeometry[],
  maxGapMeters: number,
  overlapMeters: number,
  horizontalScale: number,
  extraBoundary: Point2[] = [],
  source: RealisticRoadJoint['source'] = 'sumo_topology',
): RealisticRoadJoint | null {
  const asphalt = source === 'sumo_junction_shape'
    ? expandPolygon(extraBoundary, overlapMeters * horizontalScale)
    : convexHull([
      ...extraBoundary,
      ...endpoints.flatMap((endpoint) => endpoint.cap),
    ])
  if (asphalt.length < 3 || polygonArea(asphalt) < 0.05 * horizontalScale * horizontalScale) return null
  return {
    jointId,
    junctionId,
    kind,
    connectedEdgeIds: [...new Set(endpoints.map((endpoint) => endpoint.edgeId))].sort(),
    maxGapMeters: Number(maxGapMeters.toFixed(3)),
    overlapMeters,
    source,
    polygons: {
      sidewalk: expandPolygon(asphalt, 3.18 * horizontalScale),
      curb: expandPolygon(asphalt, 0.18 * horizontalScale),
      asphalt,
    },
  }
}

export function buildRoadJoints(input: RoadJointBuildInput): RealisticRoadJoint[] {
  const horizontalScale = Number.isFinite(input.horizontalScale) && input.horizontalScale > 0
    ? input.horizontalScale
    : 1
  const overlapMeters = input.overlapMeters ?? DEFAULT_OVERLAP_METERS
  const maximumGapMeters = input.maximumSecondaryGapMeters ?? DEFAULT_MAXIMUM_SECONDARY_GAP_METERS
  const edges = new Map(input.edges
    .filter((edge) => edge.lanes.some((lane) => lane.kind === 'driving'))
    .map((edge) => [edge.id, edge]))
  const authoritativeJunctions = new Map((input.authoritativeJunctions ?? [])
    .filter((junction) => (
      junction.junctionId !== input.primaryJunctionId
      && junction.shape.length >= 3
      && junction.internalLaneIds.length > 0
      && polygonArea(junction.shape) >= 0.05 * horizontalScale * horizontalScale
    ))
    .map((junction) => [junction.junctionId, junction]))
  const endpointMap = new Map<string, EndpointGeometry>()
  for (const topology of input.endpoints) {
    const edge = edges.get(topology.edgeId)
    if (!edge) continue
    const geometry = endpointGeometry(topology, edge, overlapMeters * horizontalScale)
    if (geometry) endpointMap.set(`${topology.junctionId}:${topology.edgeId}`, geometry)
  }

  const result: RealisticRoadJoint[] = []
  const primaryEndpoints = [...endpointMap.values()]
    .filter((endpoint) => endpoint.junctionId === input.primaryJunctionId)
  const primaryPairs = input.connections.filter((connection) => connection.junctionId === input.primaryJunctionId)
  const primaryGaps = primaryPairs.flatMap((connection) => {
    const from = endpointMap.get(`${connection.junctionId}:${connection.fromEdge}`)
    const to = endpointMap.get(`${connection.junctionId}:${connection.toEdge}`)
    return from && to ? [distance(from.sourceCenter, to.sourceCenter) / horizontalScale] : []
  })
  if (primaryEndpoints.length >= 2) {
    const primary = joint(
      `${input.primaryJunctionId}:primary`,
      input.primaryJunctionId,
      'junction',
      primaryEndpoints,
      Math.max(0, ...primaryGaps),
      overlapMeters,
      horizontalScale,
      input.primaryJunctionShape,
    )
    if (primary) result.push(primary)
  }

  const byJunction = new Map<string, RoadConnectionTopology[]>()
  for (const connection of input.connections) {
    if (connection.junctionId === input.primaryJunctionId) continue
    const from = endpointMap.get(`${connection.junctionId}:${connection.fromEdge}`)
    const to = endpointMap.get(`${connection.junctionId}:${connection.toEdge}`)
    if (!from || !to) continue
    const gapMeters = distance(from.sourceCenter, to.sourceCenter) / horizontalScale
    if (gapMeters > maximumGapMeters && !authoritativeJunctions.has(connection.junctionId)) continue
    const group = byJunction.get(connection.junctionId) ?? []
    group.push(connection)
    byJunction.set(connection.junctionId, group)
  }

  for (const [junctionId, connections] of [...byJunction].sort(([left], [right]) => left.localeCompare(right))) {
    const parents = new Map<string, string>()
    const find = (id: string): string => {
      const parent = parents.get(id) ?? id
      if (parent === id) return id
      const root = find(parent)
      parents.set(id, root)
      return root
    }
    const connect = (left: string, right: string) => {
      const leftRoot = find(left)
      const rightRoot = find(right)
      parents.set(leftRoot, rightRoot)
      parents.set(left, rightRoot)
      parents.set(right, rightRoot)
    }
    connections.forEach((connection) => connect(connection.fromEdge, connection.toEdge))
    const components = new Map<string, Set<string>>()
    for (const connection of connections) {
      for (const edgeId of [connection.fromEdge, connection.toEdge]) {
        const root = find(edgeId)
        const component = components.get(root) ?? new Set<string>()
        component.add(edgeId)
        components.set(root, component)
      }
    }
    let componentIndex = 0
    for (const edgeIds of components.values()) {
      const endpoints = [...edgeIds].flatMap((edgeId) => {
        const endpoint = endpointMap.get(`${junctionId}:${edgeId}`)
        return endpoint ? [endpoint] : []
      })
      if (endpoints.length < 2) continue
      const componentConnections = connections.filter((connection) => (
        edgeIds.has(connection.fromEdge) && edgeIds.has(connection.toEdge)
      ))
      const gaps = componentConnections.map((connection) => distance(
        endpointMap.get(`${junctionId}:${connection.fromEdge}`)!.sourceCenter,
        endpointMap.get(`${junctionId}:${connection.toEdge}`)!.sourceCenter,
      ) / horizontalScale)
      const maximumComponentGap = Math.max(0, ...gaps)
      const authoritativeJunction = maximumComponentGap > maximumGapMeters
        ? authoritativeJunctions.get(junctionId)
        : undefined
      const created = joint(
        `${junctionId}:${++componentIndex}`,
        junctionId,
        endpoints.length === 2 && !authoritativeJunction ? 'continuation' : 'junction',
        endpoints,
        maximumComponentGap,
        overlapMeters,
        horizontalScale,
        authoritativeJunction?.shape,
        authoritativeJunction ? 'sumo_junction_shape' : 'sumo_topology',
      )
      if (created) result.push(created)
    }
  }
  return result
}
