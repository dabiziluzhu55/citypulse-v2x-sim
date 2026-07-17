export interface MapConfigResponse {
  cesium_ion_token: string | null
  tianditu_enabled: boolean
}

export interface MapRuntimeConfig {
  cesiumIonToken: string
  tiandituEnabled: boolean
  tiandituProxyBaseUrl: string
  xiongan3dTilesUrl: string
}
