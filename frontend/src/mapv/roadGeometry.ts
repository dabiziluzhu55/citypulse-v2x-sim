import type { MapGeoJsonResponse } from '../types/map'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates.ts'

export type Coordinate = [number, number]
export type ProjectedCoordinate = [number, number, number]
export type RoadCoordinateProjector = (
  coordinate: readonly number[],
) => [number, number, number?]
type LocalPoint = [number, number]

export interface RoadSurfaceFeature {
  type: 'Feature'
  geometry: { type: 'Polygon'; coordinates: number[][][] }
  properties: Record<string, unknown>
}

export interface RoadLineFeature {
  type: 'Feature'
  geometry: { type: 'LineString'; coordinates: number[][] }
  properties: Record<string, unknown>
}

export interface DetailedRoadData {
  shoulders: RoadSurfaceFeature[]
  mainSurfaces: RoadSurfaceFeature[]
  secondarySurfaces: RoadSurfaceFeature[]
  junctionSurfaces: RoadSurfaceFeature[]
  outerBoundaries: RoadLineFeature[]
  medians: RoadLineFeature[]
  laneDividers: RoadLineFeature[]
  stopLines: RoadSurfaceFeature[]
  crosswalkStripes: RoadSurfaceFeature[]
}

interface NormalizedRoad {
  edgeId: string
  coordinates: Coordinate[]
  local: LocalPoint[]
  laneCount: number
  width: number
  speed: number
  priority: number
  properties: Record<string, unknown>
}

interface RoadEndpoint {
  road: NormalizedRoad
  atStart: boolean
  point: LocalPoint
  outward: LocalPoint
}

const METERS_PER_DEGREE_LATITUDE = 110_900
const DEFAULT_LANE_COUNT = 2
const DEFAULT_LANE_WIDTH_METERS = 3.35
const MIN_ROAD_WIDTH_METERS = 2.8
const MAX_ROAD_WIDTH_METERS = 40
const MIN_SEGMENT_METERS = 0.5
const MITER_LIMIT = 2.5
const PAIR_ENDPOINT_TOLERANCE_METERS = 24
const JUNCTION_CLUSTER_RADIUS_METERS = 24
const DIRECTION_DEDUP_DOT = Math.cos(12 * Math.PI / 180)

function length(point: LocalPoint): number {
  return Math.hypot(point[0], point[1])
}

function normalize(point: LocalPoint): LocalPoint {
  const magnitude = length(point) || 1
  return [point[0] / magnitude, point[1] / magnitude]
}

function subtract(a: LocalPoint, b: LocalPoint): LocalPoint {
  return [a[0] - b[0], a[1] - b[1]]
}

function add(a: LocalPoint, b: LocalPoint): LocalPoint {
  return [a[0] + b[0], a[1] + b[1]]
}

function scale(point: LocalPoint, amount: number): LocalPoint {
  return [point[0] * amount, point[1] * amount]
}

function dot(a: LocalPoint, b: LocalPoint): number {
  return a[0] * b[0] + a[1] * b[1]
}

function distance(a: LocalPoint, b: LocalPoint): number {
  return length(subtract(a, b))
}

function toLocal(point: Coordinate, origin: Coordinate): LocalPoint {
  return [
    (point[0] - origin[0]) * Math.cos(origin[1] * Math.PI / 180) * METERS_PER_DEGREE_LATITUDE,
    (point[1] - origin[1]) * METERS_PER_DEGREE_LATITUDE,
  ]
}

function toWgs84(point: LocalPoint, origin: Coordinate): Coordinate {
  return [
    origin[0] + point[0] / (Math.cos(origin[1] * Math.PI / 180) * METERS_PER_DEGREE_LATITUDE),
    origin[1] + point[1] / METERS_PER_DEGREE_LATITUDE,
  ]
}

function project(
  point: LocalPoint,
  origin: Coordinate,
  height: number,
  projector: RoadCoordinateProjector,
): ProjectedCoordinate {
  const [longitude, latitude] = toWgs84(point, origin)
  const [baiduLongitude, baiduLatitude] = projector([longitude, latitude, height])
  return [baiduLongitude, baiduLatitude, height]
}

function asLine(coordinates: unknown): Coordinate[] | null {
  if (!Array.isArray(coordinates)) return null
  const result: Coordinate[] = []
  for (const value of coordinates) {
    if (!Array.isArray(value) || typeof value[0] !== 'number' || typeof value[1] !== 'number') continue
    const point: Coordinate = [value[0], value[1]]
    if (!result.length) {
      result.push(point)
      continue
    }
    const previous = result[result.length - 1]
    const latitude = (previous[1] + point[1]) / 2
    const dx = (point[0] - previous[0]) * Math.cos(latitude * Math.PI / 180) * METERS_PER_DEGREE_LATITUDE
    const dy = (point[1] - previous[1]) * METERS_PER_DEGREE_LATITUDE
    if (Math.hypot(dx, dy) >= MIN_SEGMENT_METERS) result.push(point)
  }
  return result.length >= 2 ? result : null
}

function offsetLine(line: LocalPoint[], offset: number): LocalPoint[] {
  const segmentNormals = line.slice(0, -1).map((point, index) => {
    const tangent = normalize(subtract(line[index + 1], point))
    return [-tangent[1], tangent[0]] as LocalPoint
  })

  return line.map((point, index) => {
    if (index === 0) return add(point, scale(segmentNormals[0], offset))
    if (index === line.length - 1) return add(point, scale(segmentNormals.at(-1)!, offset))
    const previousNormal = segmentNormals[index - 1]
    const nextNormal = segmentNormals[index]
    const miter = normalize(add(previousNormal, nextNormal))
    const denominator = dot(miter, nextNormal)
    const miterLength = Math.abs(denominator) > 0.15 ? offset / denominator : offset
    const limitedLength = Math.abs(miterLength) <= Math.abs(offset) * MITER_LIMIT ? miterLength : offset
    const direction = limitedLength === miterLength ? miter : nextNormal
    return add(point, scale(direction, limitedLength))
  })
}

function polygonFromLine(
  line: LocalPoint[],
  width: number,
  origin: Coordinate,
  height: number,
  properties: Record<string, unknown>,
  projector: RoadCoordinateProjector,
): RoadSurfaceFeature {
  const left = offsetLine(line, width / 2)
  const right = offsetLine(line, -width / 2).reverse()
  const ring = [...left, ...right, left[0]]
  return polygonFeature(ring, origin, height, properties, projector)
}

function polygonFeature(
  ring: LocalPoint[],
  origin: Coordinate,
  height: number,
  properties: Record<string, unknown>,
  projector: RoadCoordinateProjector,
): RoadSurfaceFeature {
  const closed = distance(ring[0], ring.at(-1)!) < 0.001 ? ring : [...ring, ring[0]]
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [closed.map((point) => project(point, origin, height, projector))] },
    properties,
  }
}

function lineFeature(
  line: LocalPoint[],
  origin: Coordinate,
  height: number,
  properties: Record<string, unknown>,
  projector: RoadCoordinateProjector,
): RoadLineFeature {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: line.map((point) => project(point, origin, height, projector)) },
    properties,
  }
}

function rectangleFeature(
  center: LocalPoint,
  along: LocalPoint,
  alongLength: number,
  acrossLength: number,
  origin: Coordinate,
  height: number,
  properties: Record<string, unknown>,
  projector: RoadCoordinateProjector,
): RoadSurfaceFeature {
  const forward = normalize(along)
  const across: LocalPoint = [-forward[1], forward[0]]
  const halfAlong = scale(forward, alongLength / 2)
  const halfAcross = scale(across, acrossLength / 2)
  const ring = [
    add(add(center, halfAlong), halfAcross),
    add(subtract(center, halfAlong), halfAcross),
    subtract(subtract(center, halfAlong), halfAcross),
    add(subtract(center, halfAcross), halfAlong),
  ]
  return polygonFeature([...ring, ring[0]], origin, height, properties, projector)
}

function circleFeature(
  center: LocalPoint,
  radius: number,
  origin: Coordinate,
  properties: Record<string, unknown>,
  projector: RoadCoordinateProjector,
): RoadSurfaceFeature {
  const ring = Array.from({ length: 24 }, (_, index) => {
    const angle = index / 24 * Math.PI * 2
    return [center[0] + Math.cos(angle) * radius, center[1] + Math.sin(angle) * radius] as LocalPoint
  })
  return polygonFeature([...ring, ring[0]], origin, 0.15, properties, projector)
}

function normalizeRoads(response: MapGeoJsonResponse, origin: Coordinate): NormalizedRoad[] {
  return (response.geojson?.features ?? [])
    .filter((feature) => feature.geometry?.type === 'LineString')
    .map((feature) => {
      const coordinates = asLine(feature.geometry.coordinates)
      if (!coordinates) return null
      const properties = feature.properties ?? {}
      const laneCount = Math.max(1, Math.floor(Number(properties.lane_count) || DEFAULT_LANE_COUNT))
      const explicitWidth = Number(properties.width_m)
      const width = Math.min(
        MAX_ROAD_WIDTH_METERS,
        Math.max(
          MIN_ROAD_WIDTH_METERS,
          Number.isFinite(explicitWidth) && explicitWidth > 0
            ? explicitWidth
            : laneCount * DEFAULT_LANE_WIDTH_METERS,
        ),
      )
      return {
        edgeId: String(
          properties.edge_id ??
          `${coordinates[0][0]},${coordinates[0][1]}:${coordinates.at(-1)![0]},${coordinates.at(-1)![1]}`,
        ),
        coordinates,
        local: coordinates.map((point) => toLocal(point, origin)),
        laneCount,
        width,
        speed: Number(properties.speed) || 0,
        priority: Number(properties.priority) || 0,
        properties,
      }
    })
    .filter((road): road is NormalizedRoad => road !== null)
    .sort((a, b) => a.edgeId.localeCompare(b.edgeId) || a.coordinates.length - b.coordinates.length)
}

function roadDirection(road: NormalizedRoad): LocalPoint {
  return normalize(subtract(road.local.at(-1)!, road.local[0]))
}

function pairRoads(roads: NormalizedRoad[]): Array<[NormalizedRoad, NormalizedRoad]> {
  const pairs: Array<[NormalizedRoad, NormalizedRoad]> = []
  const used = new Set<string>()
  for (let aIndex = 0; aIndex < roads.length; aIndex += 1) {
    const a = roads[aIndex]
    if (used.has(a.edgeId)) continue
    for (let bIndex = aIndex + 1; bIndex < roads.length; bIndex += 1) {
      const b = roads[bIndex]
      if (used.has(b.edgeId)) continue
      const reversedEndpoints =
        distance(a.local[0], b.local.at(-1)!) <= PAIR_ENDPOINT_TOLERANCE_METERS &&
        distance(a.local.at(-1)!, b.local[0]) <= PAIR_ENDPOINT_TOLERANCE_METERS
      const opposite = dot(roadDirection(a), roadDirection(b)) <= -0.85
      const aLength = distance(a.local[0], a.local.at(-1)!)
      const bLength = distance(b.local[0], b.local.at(-1)!)
      const similarLength = Math.min(aLength, bLength) / Math.max(aLength, bLength) >= 0.65
      if (!reversedEndpoints || !opposite || !similarLength) continue
      pairs.push([a, b])
      used.add(a.edgeId)
      used.add(b.edgeId)
      break
    }
  }
  return pairs
}

function facingBoundary(a: NormalizedRoad, b: NormalizedRoad): LocalPoint[] {
  const midpoint = b.local[Math.floor(b.local.length / 2)]
  const left = offsetLine(a.local, a.width / 2)
  const right = offsetLine(a.local, -a.width / 2)
  const leftMiddle = left[Math.floor(left.length / 2)]
  const rightMiddle = right[Math.floor(right.length / 2)]
  return distance(leftMiddle, midpoint) <= distance(rightMiddle, midpoint) ? left : right
}

function endpointRecords(roads: NormalizedRoad[]): RoadEndpoint[] {
  return roads.flatMap((road) => [
    {
      road,
      atStart: true,
      point: road.local[0],
      outward: normalize(subtract(road.local[1], road.local[0])),
    },
    {
      road,
      atStart: false,
      point: road.local.at(-1)!,
      outward: normalize(subtract(road.local.at(-2)!, road.local.at(-1)!)),
    },
  ])
}

function clusterEndpoints(endpoints: RoadEndpoint[]): RoadEndpoint[][] {
  const parent = endpoints.map((_, index) => index)
  const find = (index: number): number => {
    while (parent[index] !== index) {
      parent[index] = parent[parent[index]]
      index = parent[index]
    }
    return index
  }
  const union = (a: number, b: number): void => {
    const rootA = find(a)
    const rootB = find(b)
    if (rootA !== rootB) parent[rootB] = rootA
  }
  for (let a = 0; a < endpoints.length; a += 1) {
    for (let b = a + 1; b < endpoints.length; b += 1) {
      if (distance(endpoints[a].point, endpoints[b].point) <= JUNCTION_CLUSTER_RADIUS_METERS) union(a, b)
    }
  }
  const groups = new Map<number, RoadEndpoint[]>()
  endpoints.forEach((endpoint, index) => {
    const root = find(index)
    groups.set(root, [...(groups.get(root) ?? []), endpoint])
  })
  return [...groups.values()]
    .filter((group) => group.length >= 3)
    .sort((a, b) => {
      const aKey = a.map((item) => item.road.edgeId).sort().join(':')
      const bKey = b.map((item) => item.road.edgeId).sort().join(':')
      return aKey.localeCompare(bKey)
    })
}

function uniqueApproaches(group: RoadEndpoint[]): RoadEndpoint[] {
  const sorted = [...group].sort((a, b) => {
    const angleA = Math.atan2(a.outward[1], a.outward[0])
    const angleB = Math.atan2(b.outward[1], b.outward[0])
    return angleA - angleB || b.road.width - a.road.width || a.road.edgeId.localeCompare(b.road.edgeId)
  })
  const approaches: RoadEndpoint[] = []
  for (const endpoint of sorted) {
    const duplicate = approaches.find((item) => dot(item.outward, endpoint.outward) >= DIRECTION_DEDUP_DOT)
    if (!duplicate) approaches.push(endpoint)
    else if (endpoint.road.width > duplicate.road.width) approaches[approaches.indexOf(duplicate)] = endpoint
  }
  return approaches
}

function addJunctionGeometry(
  roads: NormalizedRoad[],
  origin: Coordinate,
  data: DetailedRoadData,
  projector: RoadCoordinateProjector,
): void {
  const clusters = clusterEndpoints(endpointRecords(roads))
  for (const [junctionIndex, group] of clusters.entries()) {
    const approaches = uniqueApproaches(group)
    if (approaches.length < 3) continue
    const center = group.reduce<LocalPoint>(
      (sum, endpoint) => add(sum, endpoint.point),
      [0, 0],
    ).map((value) => value / group.length) as LocalPoint
    const radius = Math.max(
      ...group.map((endpoint) => distance(center, endpoint.point) + endpoint.road.width / 2),
    ) + 1.5
    data.junctionSurfaces.push(circleFeature(center, radius, origin, {
      generated_layer: 'junction-surface',
      junction_index: junctionIndex,
      source: 'sumo',
    }, projector))

    for (const approach of approaches) {
      const stopCenter = add(approach.point, scale(approach.outward, 1.4))
      data.stopLines.push(rectangleFeature(
        stopCenter,
        approach.outward,
        0.42,
        approach.road.width,
        origin,
        0.26,
        { ...approach.road.properties, generated_layer: 'stop-line', junction_index: junctionIndex },
        projector,
      ))
      for (let stripeIndex = 0; stripeIndex < 5; stripeIndex += 1) {
        const stripeCenter = add(approach.point, scale(approach.outward, 2.4 + stripeIndex * 0.9))
        data.crosswalkStripes.push(rectangleFeature(
          stripeCenter,
          approach.outward,
          0.45,
          approach.road.width,
          origin,
          0.28,
          {
            ...approach.road.properties,
            generated_layer: 'crosswalk-stripe',
            junction_index: junctionIndex,
            stripe_index: stripeIndex,
          },
          projector,
        ))
      }
    }
  }
}

export function buildDetailedRoadData(
  response: MapGeoJsonResponse,
  projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
): DetailedRoadData {
  const origin: Coordinate = [response.center.longitude, response.center.latitude]
  const roads = normalizeRoads(response, origin)
  const data: DetailedRoadData = {
    shoulders: [],
    mainSurfaces: [],
    secondarySurfaces: [],
    junctionSurfaces: [],
    outerBoundaries: [],
    medians: [],
    laneDividers: [],
    stopLines: [],
    crosswalkStripes: [],
  }

  for (const road of roads) {
    const layerProperties = { ...road.properties, source: 'sumo' }
    data.shoulders.push(polygonFromLine(
      road.local,
      road.width + 1.2,
      origin,
      0.1,
      { ...layerProperties, generated_layer: 'road-shoulder' },
      projector,
    ))
    const surface = polygonFromLine(
      road.local,
      road.width,
      origin,
      0.14,
      { ...layerProperties, generated_layer: 'road-surface' },
      projector,
    )
    const isMainRoad = road.laneCount >= 2 || road.speed >= 20 || road.priority >= 2
    ;(isMainRoad ? data.mainSurfaces : data.secondarySurfaces).push(surface)

    data.outerBoundaries.push(
      lineFeature(offsetLine(road.local, road.width / 2), origin, 0.2, {
        ...layerProperties,
        generated_layer: 'outer-boundary',
        side: 'left',
      }, projector),
      lineFeature(offsetLine(road.local, -road.width / 2), origin, 0.2, {
        ...layerProperties,
        generated_layer: 'outer-boundary',
        side: 'right',
      }, projector),
    )

    const laneWidth = road.width / road.laneCount
    for (let dividerIndex = 1; dividerIndex < road.laneCount; dividerIndex += 1) {
      const offset = -road.width / 2 + laneWidth * dividerIndex
      data.laneDividers.push(lineFeature(offsetLine(road.local, offset), origin, 0.24, {
        ...layerProperties,
        generated_layer: 'lane-divider',
        divider_index: dividerIndex,
      }, projector))
    }
  }

  for (const [a, b] of pairRoads(roads)) {
    data.medians.push(lineFeature(facingBoundary(a, b), origin, 0.22, {
      edge_id: a.edgeId,
      paired_edge_id: b.edgeId,
      generated_layer: 'median',
      source: 'sumo',
    }, projector))
  }

  // ponytail: endpoint clustering approximates junction topology; replace it when the API exposes node polygons.
  addJunctionGeometry(roads, origin, data, projector)
  return data
}
