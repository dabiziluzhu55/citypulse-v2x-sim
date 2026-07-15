import * as Cesium from 'cesium'
import type Map from 'ol/Map'
import { easeOut } from 'ol/easing'
import { fromLonLat, transformExtent } from 'ol/proj'
import { MAP_FIT_PADDING, MAP_FLY_DURATION_MS } from '../constants/mapLayout'

export type StoredMapViewport =
  | { kind: 'center'; center: [number, number]; zoom: number }
  | { kind: 'bounds'; bounds: [number, number, number, number] }

export interface ApplyViewportOptions {
  duration?: number
}

const CESIUM_CAMERA_PITCH = Cesium.Math.toRadians(-45)

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

export function applyCesiumViewport(
  viewer: Cesium.Viewer,
  viewport: StoredMapViewport,
  options: ApplyViewportOptions = {},
): void {
  const durationSec = (options.duration ?? MAP_FLY_DURATION_MS) / 1000

  if (viewport.kind === 'bounds') {
    const [minLon, minLat, maxLon, maxLat] = viewport.bounds
    void viewer.camera.flyTo({
      destination: Cesium.Rectangle.fromDegrees(minLon, minLat, maxLon, maxLat),
      duration: durationSec,
    })
    return
  }

  const [lon, lat] = viewport.center
  const height = zoomToCameraHeight(viewport.zoom, lat)
  void viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat, height),
    orientation: {
      heading: 0,
      pitch: CESIUM_CAMERA_PITCH,
      roll: 0,
    },
    duration: durationSec,
  })
}
