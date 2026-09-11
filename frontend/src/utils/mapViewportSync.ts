import type Map from 'ol/Map'
import { easeOut } from 'ol/easing'
import { fromLonLat, transformExtent } from 'ol/proj'
import { MAP_FIT_PADDING, MAP_FLY_DURATION_MS } from '../constants/mapLayout'
import { DEFAULT_CESIUM_CAMERA_PRESET_ID, resolveCesiumCameraPreset } from '../constants/mapDefaults'
import type { CesiumCameraPreset } from '../types/map'

export type StoredMapViewport =
  | { kind: 'center'; center: [number, number]; zoom: number }
  | { kind: 'bounds'; bounds: [number, number, number, number] }

export interface ApplyViewportOptions {
  duration?: number
  cameraPreset?: CesiumCameraPreset
  force?: boolean
  complete?: () => void
}

export function zoomToCameraHeight(zoom: number, latitude: number): number {
  const latRad = (latitude * Math.PI) / 180
  const height = (40075016.686 * Math.cos(latRad)) / Math.pow(2, zoom + 1)
  return Math.max(height, 200)
}

export function applyOlViewport(
  map: Map,
  viewport: StoredMapViewport,
  options: ApplyViewportOptions = {},
): void {
  const duration = options.duration ?? MAP_FLY_DURATION_MS
  const view = map.getView()

  if (viewport.kind === 'bounds') {
    const extent = transformExtent(viewport.bounds, 'EPSG:4326', 'EPSG:3857')
    view.fit(extent, {
      padding: MAP_FIT_PADDING,
      duration,
      easing: easeOut,
      maxZoom: 16,
    })
    return
  }

  view.animate({
    center: fromLonLat(viewport.center),
    zoom: viewport.zoom,
    duration,
    easing: easeOut,
  })
}

export function resolveThreeMapCameraPreset(
  presetId = DEFAULT_CESIUM_CAMERA_PRESET_ID,
): CesiumCameraPreset {
  return resolveCesiumCameraPreset(presetId)
}
