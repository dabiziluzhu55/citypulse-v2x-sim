import type { InjectionKey } from 'vue'
import type Map from 'ol/Map'
import type { Viewer } from 'cesium'
import type { StoredMapViewport } from '../utils/mapViewportSync'

export type AppMapMode = 'explore' | 'anchored'
export type MapDimension = '2d' | '3d'
export type CesiumCameraPresetId = 'overview' | 'birdseye' | 'traffic-top' | 'road-cruise' | 'intersection'

export interface CesiumCameraPreset {
  id: CesiumCameraPresetId
  label: string
  shortLabel: string
  description: string
  height: number
  pitchDegrees: number
  headingDegrees: number
  rollDegrees?: number
  rangeMultiplier?: number
  maxCameraHeight?: number
  localViewRadiusMeters?: number
  minimumZoomDistance?: number
  maximumZoomDistance?: number
}

export interface MapViewport {
  center: [number, number]
  zoom: number
  bounds?: [number, number, number, number]
}

export interface AppMapView {
  mode: { value: AppMapMode }
  dimension: { value: MapDimension }
  cameraPreset: { value: CesiumCameraPresetId }
  anchorId: { value: string | null }
  viewport: { value: StoredMapViewport }
  setDimension: (next: MapDimension) => void
  setCameraPreset: (next: CesiumCameraPresetId) => void
  registerMap: (map: Map) => void
  unregisterMap: () => void
  registerCesium: (viewer: Viewer) => void
  unregisterCesium: () => void
  flyTo: (center: [number, number], zoom: number, anchorId?: string) => void
  fitBounds: (bounds: [number, number, number, number], anchorId?: string) => void
  flyToTemplate: (templateId: string) => void
  flyToScenario: (scenarioId: string, templateId?: string) => void
  resetToDefault: () => void
}

export const appMapViewKey: InjectionKey<AppMapView> = Symbol('appMapView')

export interface MapGeoJsonCenter {
  longitude: number
  latitude: number
}

export interface MapGeoJsonBounds {
  west: number
  south: number
  east: number
  north: number
}

export interface MapGeoJsonResponse {
  intersection_id: string
  center: MapGeoJsonCenter
  radius_m: number
  bounds: MapGeoJsonBounds
  geojson: GeoJsonFeatureCollection
}

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection'
  features: GeoJsonFeature[]
}

export interface GeoJsonFeature {
  type: 'Feature'
  properties: Record<string, unknown>
  geometry: {
    type: string
    coordinates: unknown
  }
}
