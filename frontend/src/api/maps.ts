import { apiClient } from './client'
import type { MapGeoJsonResponse } from '../types/map'

export async function fetchMapGeoJson(
  intersectionId: string,
  radiusM = 600,
): Promise<MapGeoJsonResponse> {
  const { data } = await apiClient.get<MapGeoJsonResponse>(
    `/maps/${intersectionId}/geojson`,
    { params: { radius_m: radiusM } },
  )
  return data
}
