import type { MapGeoJsonResponse } from '../types/map'
import { wgs84ToBd09 } from './sceneCoordinates.ts'

const COORDINATE_TOLERANCE = 1e-9

function isCoordinatePair(value: unknown): value is [number, number] {
  return Array.isArray(value)
    && value.length >= 2
    && Number.isFinite(value[0])
    && Number.isFinite(value[1])
}

function coordinatesMatch(
  actual: unknown,
  expected: readonly [number, number],
): boolean {
  return isCoordinatePair(actual)
    && Math.abs(actual[0] - expected[0]) <= COORDINATE_TOLERANCE
    && Math.abs(actual[1] - expected[1]) <= COORDINATE_TOLERANCE
}

export interface StaticRoadTilesetManifest {
  intersection_id: string
  placement_mode: string
  placement_bd09: [number, number]
  radius_m: number
  source_generated_at: unknown
  source_vertex_count: unknown
  feature_count: number
  source_sha256: string
  coordinate_system: string
  origin_wgs84: [number, number]
}

export function roadTilesetManifestIsValid(
  manifest: StaticRoadTilesetManifest,
  scenePlacement: string,
): boolean {
  if (!isCoordinatePair(manifest.origin_wgs84)) return false
  return scenePlacement === 'actual'
    && manifest.placement_mode === scenePlacement
    && manifest.coordinate_system === 'LOCAL_BD09_WEB_MERCATOR_METERS'
    && coordinatesMatch(manifest.placement_bd09, wgs84ToBd09(...manifest.origin_wgs84))
}

export function roadTilesetMatchesResponse(
  manifest: StaticRoadTilesetManifest,
  response: MapGeoJsonResponse,
  scenePlacement: string,
): boolean {
  const metadata = response.geojson.metadata ?? {}
  const roadFeatureCount = response.geojson.features.filter(
    (feature) => feature.geometry.type === 'LineString',
  ).length
  const sourceCenter: [number, number] = [
    response.center.longitude,
    response.center.latitude,
  ]
  return manifest.intersection_id === response.intersection_id
    && roadTilesetManifestIsValid(manifest, scenePlacement)
    && coordinatesMatch(manifest.origin_wgs84, sourceCenter)
    && Number(manifest.radius_m) === Number(response.radius_m)
    && manifest.feature_count === roadFeatureCount
    && manifest.source_generated_at === (metadata.generated_at ?? null)
    && manifest.source_vertex_count === (metadata.vertex_count ?? null)
}
