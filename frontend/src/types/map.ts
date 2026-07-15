import type { InjectionKey } from 'vue'

import type Map from 'ol/Map'

import type { Viewer } from 'cesium'

import type { StoredMapViewport } from '../utils/mapViewportSync'



export type AppMapMode = 'explore' | 'anchored'

export type MapDimension = '2d' | '3d'



export interface MapViewport {

  center: [number, number]

  zoom: number

  bounds?: [number, number, number, number]

}



export interface AppMapView {

  mode: { value: AppMapMode }

  dimension: { value: MapDimension }

  anchorId: { value: string | null }

  viewport: { value: StoredMapViewport }

  setDimension: (next: MapDimension) => void

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

