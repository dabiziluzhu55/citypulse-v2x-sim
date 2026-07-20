import type { MapGeoJsonResponse } from '../types/map'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'

export type Coordinate = [number, number]
export type ProjectedCoordinate = [number, number, number]

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
  surfaces: RoadSurfaceFeature[]
  boundaries: RoadLineFeature[]
  dividers: RoadLineFeature[]
  junctions: RoadLineFeature[]
}

const METERS_PER_DEGREE_LATITUDE = 110_900
const MAX_ROAD_WIDTH_METERS = 40
const DEFAULT_LANE_COUNT = 2
const DEFAULT_LANE_WIDTH_METERS = 3.35
const MIN_ROAD_WIDTH_METERS = 2.8
const DUPLICATE_DISTANCE_METERS = 1.2

function asLine(coordinates: unknown): Coordinate[] | null {
  if (!Array.isArray(coordinates)) return null
  const line = coordinates.filter(
    (point): point is number[] =>
      Array.isArray(point) && typeof point[0] === 'number' && typeof point[1] === 'number',
  ).map(([longitude, latitude]) => [longitude, latitude] as Coordinate)
  const result: Coordinate[] = []
  for (const point of line) {
    if (!result.length || distanceMeters(result[result.length - 1], point) > 0.05) result.push(point)
  }
  return result.length >= 2 ? result : null
}

function distanceMeters(a: Coordinate, b: Coordinate): number {
  const latitude = (a[1] + b[1]) / 2
  const x = (b[0] - a[0]) * Math.cos(latitude * Math.PI / 180) * METERS_PER_DEGREE_LATITUDE
  const y = (b[1] - a[1]) * METERS_PER_DEGREE_LATITUDE
  return Math.hypot(x, y)
}

function localVector(a: Coordinate, b: Coordinate): Coordinate {
  const latitude = (a[1] + b[1]) / 2
  return [
    (b[0] - a[0]) * Math.cos(latitude * Math.PI / 180) * METERS_PER_DEGREE_LATITUDE,
    (b[1] - a[1]) * METERS_PER_DEGREE_LATITUDE,
  ]
}

function offsetPoint(point: Coordinate, normal: Coordinate, distance: number): Coordinate {
  return [
    point[0] + normal[0] * distance / (Math.cos(point[1] * Math.PI / 180) * METERS_PER_DEGREE_LATITUDE),
    point[1] + normal[1] * distance / METERS_PER_DEGREE_LATITUDE,
  ]
}

function resolveWidth(properties: Record<string, unknown>): number {
  const explicit = Number(properties.width_m)
  const lanes = Number(properties.lane_count) || DEFAULT_LANE_COUNT
  const width = Number.isFinite(explicit) && explicit > 0 ? explicit : lanes * DEFAULT_LANE_WIDTH_METERS
  return Math.min(MAX_ROAD_WIDTH_METERS, Math.max(MIN_ROAD_WIDTH_METERS, width))
}

function project(point: Coordinate, height: number): ProjectedCoordinate {
  const [longitude, latitude] = projectSimulationCoordinateToBaiduMap(point)
  return [longitude, latitude, height]
}

function createPolygon(line: Coordinate[], width: number, properties: Record<string, unknown>): RoadSurfaceFeature | null {
  const left: Coordinate[] = []
  const right: Coordinate[] = []
  for (let index = 0; index < line.length; index += 1) {
    const previous = line[Math.max(0, index - 1)]
    const next = line[Math.min(line.length - 1, index + 1)]
    const vector = localVector(previous, next)
    const length = Math.hypot(vector[0], vector[1]) || 1
    const normal: Coordinate = [-vector[1] / length, vector[0] / length]
    left.push(offsetPoint(line[index], normal, width / 2))
    right.push(offsetPoint(line[index], normal, -width / 2))
  }
  const ring = [...left, ...right.reverse(), left[0]]
  return ring.length >= 4 ? {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [ring.map((point) => project(point, 0.18))] },
    properties,
  } : null
}

function createLine(line: Coordinate[], height: number, properties: Record<string, unknown>): RoadLineFeature {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: line.map((point) => project(point, height)) },
    properties,
  }
}

function endpointKey(line: Coordinate[]): string {
  const first = line[0]
  const last = line[line.length - 1]
  const forward = `${first[0].toFixed(6)},${first[1].toFixed(6)}:${last[0].toFixed(6)},${last[1].toFixed(6)}`
  const reverse = `${last[0].toFixed(6)},${last[1].toFixed(6)}:${first[0].toFixed(6)},${first[1].toFixed(6)}`
  return forward < reverse ? forward : reverse
}

function isDuplicate(line: Coordinate[], accepted: Coordinate[][]): boolean {
  const first = line[0]
  const last = line[line.length - 1]
  return accepted.some((candidate) => {
    const sameDirection = distanceMeters(first, candidate[0]) < DUPLICATE_DISTANCE_METERS && distanceMeters(last, candidate[candidate.length - 1]) < DUPLICATE_DISTANCE_METERS
    const reverseDirection = distanceMeters(first, candidate[candidate.length - 1]) < DUPLICATE_DISTANCE_METERS && distanceMeters(last, candidate[0]) < DUPLICATE_DISTANCE_METERS
    return sameDirection || reverseDirection
  })
}

export function buildDetailedRoadData(response: MapGeoJsonResponse): DetailedRoadData {
  const surfaces: RoadSurfaceFeature[] = []
  const boundaries: RoadLineFeature[] = []
  const dividers: RoadLineFeature[] = []
  const junctions: RoadLineFeature[] = []
  const accepted: Coordinate[][] = []
  const keys = new Set<string>()

  for (const feature of response.geojson?.features ?? []) {
    if (feature.geometry?.type !== 'LineString') {
      if (feature.geometry?.type === 'Point') {
        const point = feature.geometry.coordinates
        if (Array.isArray(point) && typeof point[0] === 'number' && typeof point[1] === 'number') {
          const center: Coordinate = [point[0], point[1]]
          junctions.push(createLine([center, center], 0.38, feature.properties ?? {}))
        }
      }
      continue
    }
    const line = asLine(feature.geometry.coordinates)
    if (!line || isDuplicate(line, accepted)) continue
    const key = endpointKey(line)
    if (keys.has(key)) continue
    keys.add(key)
    accepted.push(line)
    const properties = feature.properties ?? {}
    const width = resolveWidth(properties)
    const surface = createPolygon(line, width, properties)
    if (!surface) continue
    surfaces.push(surface)
    boundaries.push(createLine(line, 0.28, properties))
    if ((Number(properties.lane_count) || DEFAULT_LANE_COUNT) > 1) {
      dividers.push(createLine(line, 0.34, { ...properties, marking: 'lane-divider' }))
    }
  }

  return { surfaces, boundaries, dividers, junctions }
}
