import type {
  Point2,
  RealisticRoadEdge,
  RealisticRoadJoint,
  RoadSurfacePolygon,
} from './intersectionManifest.ts'
import polygonClipping from 'polygon-clipping'
import {
  edgeCenterline,
  edgeRoadWidth,
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
  normal: Point2
  roadWidth: number
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
  return {
    ...topology,
    sourceCenter,
    center,
    normal,
    roadWidth: edgeRoadWidth(edge),
  }
}

function polygonArea(points: Point2[]): number {
  return Math.abs(points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]
    return sum + point[0] * next[1] - next[0] * point[1]
  }, 0)) / 2
}

function cap(endpoint: EndpointGeometry, halfWidth: number, at: 'source' | 'inner'): [Point2, Point2] {
  const center = at === 'source' ? endpoint.sourceCenter : endpoint.center
  return [
    [center[0] + endpoint.normal[0] * halfWidth, center[1] + endpoint.normal[1] * halfWidth],
    [center[0] - endpoint.normal[0] * halfWidth, center[1] - endpoint.normal[1] * halfWidth],
  ]
}

function endpointSleeve(endpoint: EndpointGeometry, halfWidth: number): Point2[] {
  const source = cap(endpoint, halfWidth, 'source')
  const inner = cap(endpoint, halfWidth, 'inner')
  return [source[0], inner[0], inner[1], source[1]]
}

function connectionSleeve(left: EndpointGeometry, right: EndpointGeometry, padding: number): Point2[] {
  const leftCap = cap(left, left.roadWidth / 2 + padding, 'source')
  const rightCap = cap(right, right.roadWidth / 2 + padding, 'source')
  const parallel = distance(leftCap[0], rightCap[0]) + distance(leftCap[1], rightCap[1])
  const crossed = distance(leftCap[0], rightCap[1]) + distance(leftCap[1], rightCap[0])
  return crossed < parallel
    ? [leftCap[0], rightCap[1], rightCap[0], leftCap[1]]
    : [leftCap[0], rightCap[0], rightCap[1], leftCap[1]]
}

function bufferedPolygon(points: Point2[], padding: number): Point2[][] {
  if (points.length < 3) return []
  if (padding <= 1e-6) return [points]
  const orientation = points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]
    return sum + point[0] * next[1] - next[0] * point[1]
  }, 0) >= 0 ? 1 : -1
  const offsetLines = points.map((point, index) => {
    const next = points[(index + 1) % points.length]
    const dx = next[0] - point[0]
    const dy = next[1] - point[1]
    const length = Math.hypot(dx, dy) || 1
    const normal: Point2 = orientation > 0
      ? [dy / length, -dx / length]
      : [-dy / length, dx / length]
    return {
      point: [point[0] + normal[0] * padding, point[1] + normal[1] * padding] as Point2,
      direction: [dx, dy] as Point2,
      normal,
    }
  })
  const expanded = points.map<Point2>((point, index) => {
    const previous = offsetLines[(index - 1 + offsetLines.length) % offsetLines.length]
    const next = offsetLines[index]
    const cross = previous.direction[0] * next.direction[1] - previous.direction[1] * next.direction[0]
    if (Math.abs(cross) <= 1e-8) {
      return [
        point[0] + (previous.normal[0] + next.normal[0]) / 2 * padding,
        point[1] + (previous.normal[1] + next.normal[1]) / 2 * padding,
      ] as Point2
    }
    const deltaX = next.point[0] - previous.point[0]
    const deltaY = next.point[1] - previous.point[1]
    const ratio = (deltaX * next.direction[1] - deltaY * next.direction[0]) / cross
    const intersection: Point2 = [
      previous.point[0] + previous.direction[0] * ratio,
      previous.point[1] + previous.direction[1] * ratio,
    ]
    const miterDistance = distance(point, intersection)
    if (miterDistance <= padding * 4) return intersection
    const scale = padding * 4 / miterDistance
    return [
      point[0] + (intersection[0] - point[0]) * scale,
      point[1] + (intersection[1] - point[1]) * scale,
    ]
  })
  return [expanded]
}

function cleanPolygon(points: Point2[]): Point2[] {
  const cleaned: Point2[] = []
  for (const [rawX, rawY] of points) {
    const point: Point2 = [Number(rawX.toFixed(6)), Number(rawY.toFixed(6))]
    const previous = cleaned.at(-1)
    if (previous && distance(previous, point) <= 1e-6) continue
    cleaned.push(point)
  }
  if (cleaned.length > 2 && distance(cleaned[0], cleaned.at(-1)!) <= 1e-6) cleaned.pop()
  return cleaned
}

function unionPolygons(polygons: Point2[][]): RoadSurfacePolygon[] {
  const valid = polygons
    .map(cleanPolygon)
    .filter((polygon) => polygon.length >= 3 && polygonArea(polygon) > 1e-6)
  if (valid.length === 0) return []
  const [first, ...rest] = valid.map((polygon) => [polygon])
  return polygonClipping.union(first, ...rest)
    .map((polygon) => ({
      outer: polygon[0].slice(0, -1) as Point2[],
      holes: polygon.slice(1).map((ring) => ring.slice(0, -1) as Point2[]),
    }))
    .filter((part) => part.outer.length >= 3)
}

function largestOuter(parts: RoadSurfacePolygon[]): Point2[] {
  return [...parts].sort((left, right) => polygonArea(right.outer) - polygonArea(left.outer))[0]?.outer ?? []
}

function joint(
  jointId: string,
  junctionId: string,
  kind: RealisticRoadJoint['kind'],
  endpoints: EndpointGeometry[],
  connectionPairs: Array<[EndpointGeometry, EndpointGeometry]>,
  maxGapMeters: number,
  overlapMeters: number,
  horizontalScale: number,
  extraBoundary: Point2[] = [],
  source: RealisticRoadJoint['source'] = 'sumo_topology',
): RealisticRoadJoint | null {
  const layerParts = (paddingMeters: number) => {
    const padding = paddingMeters * horizontalScale
    return unionPolygons([
      ...(source === 'sumo_junction_shape' ? bufferedPolygon(extraBoundary, padding) : []),
      ...endpoints.map((endpoint) => endpointSleeve(endpoint, endpoint.roadWidth / 2 + padding)),
      ...connectionPairs.map(([left, right]) => connectionSleeve(left, right, padding)),
    ])
  }
  const surfaceParts = {
    sidewalk: layerParts(3.18),
    curb: layerParts(0.18),
    asphalt: layerParts(0),
  }
  const asphalt = largestOuter(surfaceParts.asphalt)
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
      sidewalk: largestOuter(surfaceParts.sidewalk),
      curb: largestOuter(surfaceParts.curb),
      asphalt,
    },
    surfaceParts,
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
  const primaryConnectionPairs = primaryPairs.flatMap((connection) => {
    const from = endpointMap.get(`${connection.junctionId}:${connection.fromEdge}`)
    const to = endpointMap.get(`${connection.junctionId}:${connection.toEdge}`)
    return from && to ? [[from, to] as [EndpointGeometry, EndpointGeometry]] : []
  })
  if (primaryEndpoints.length >= 2) {
    const primary = joint(
      `${input.primaryJunctionId}:primary`,
      input.primaryJunctionId,
      'junction',
      primaryEndpoints,
      primaryConnectionPairs,
      Math.max(0, ...primaryGaps),
      overlapMeters,
      horizontalScale,
      input.primaryJunctionShape,
      'sumo_junction_shape',
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
        componentConnections.flatMap((connection) => {
          const from = endpointMap.get(`${junctionId}:${connection.fromEdge}`)
          const to = endpointMap.get(`${junctionId}:${connection.toEdge}`)
          return from && to ? [[from, to] as [EndpointGeometry, EndpointGeometry]] : []
        }),
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
