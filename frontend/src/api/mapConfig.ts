import { apiClient } from './client'
import type { MapConfigResponse, MapRuntimeConfig } from '../types/mapConfig'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

let cachedConfig: MapRuntimeConfig | null = null

export async function fetchMapConfig(): Promise<MapRuntimeConfig> {
  if (cachedConfig) {
    return cachedConfig
  }

  const { data } = await apiClient.get<MapConfigResponse>('/config/map')
  cachedConfig = {
    cesiumIonToken: data.cesium_ion_token?.trim() ?? '',
    tiandituEnabled: data.tianditu_enabled,
    tiandituProxyBaseUrl: `${API_BASE_URL}/tiles/tianditu`,
    xiongan3dTilesUrl:
      import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim() || '/3dtiles/xiongan/tileset.json',
  }
  return cachedConfig
}

export function resetMapConfigCache(): void {
  cachedConfig = null
}
