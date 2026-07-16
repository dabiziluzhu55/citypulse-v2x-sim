import * as Cesium from 'cesium'
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
}

const DEFAULT_CAMERA_PRESET = resolveCesiumCameraPreset(DEFAULT_CESIUM_CAMERA_PRESET_ID)

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

function boundsCenter(bounds: [number, number, number, number]): [number, number] {
  const [minLon, minLat, maxLon, maxLat] = bounds
  return [(minLon + maxLon) / 2, (minLat + maxLat) / 2]
}

function boundsCameraHeight(
  bounds: [number, number, number, number],
  preset: CesiumCameraPreset,
): number {
  const [minLon, minLat, maxLon, maxLat] = bounds
  const widthMeters = Cesium.Cartesian3.distance(
    Cesium.Cartesian3.fromDegrees(minLon, (minLat + maxLat) / 2),
    Cesium.Cartesian3.fromDegrees(maxLon, (minLat + maxLat) / 2),
  )
  const heightMeters = Cesium.Cartesian3.distance(
    Cesium.Cartesian3.fromDegrees((minLon + maxLon) / 2, minLat),
    Cesium.Cartesian3.fromDegrees((minLon + maxLon) / 2, maxLat),
  )
  const range = Math.max(widthMeters, heightMeters) * (preset.rangeMultiplier ?? 1)
  return Math.max(preset.height, range)
}

function cruiseCameraHeight(preset: CesiumCameraPreset): number {
  const radiusBasedHeight = (preset.localViewRadiusMeters ?? preset.height) * 0.7
  const desiredHeight = Math.max(preset.height, radiusBasedHeight)
  return preset.maxCameraHeight === undefined
    ? desiredHeight
    : Math.min(desiredHeight, preset.maxCameraHeight)
}

function cameraOrientation(preset: CesiumCameraPreset): Cesium.HeadingPitchRollValues {
  return {
    heading: Cesium.Math.toRadians(preset.headingDegrees),
    pitch: Cesium.Math.toRadians(preset.pitchDegrees),
    roll: Cesium.Math.toRadians(preset.rollDegrees ?? 0),
  }
}

export function applyCesiumViewport(
  viewer: Cesium.Viewer,
  viewport: StoredMapViewport,
  options: ApplyViewportOptions = {},
): void {
  const durationSec = (options.duration ?? MAP_FLY_DURATION_MS) / 1000
  const preset = options.cameraPreset ?? DEFAULT_CAMERA_PRESET

  if (viewport.kind === 'bounds') {
    const [lon, lat] = boundsCenter(viewport.bounds)
    const height = preset.id === 'road-cruise'
      ? cruiseCameraHeight(preset)
      : boundsCameraHeight(viewport.bounds, preset)
    void viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(lon, lat, height),
      orientation: cameraOrientation(preset),
      duration: durationSec,
      complete: () => viewer.scene.requestRender(),
    })
    return
  }

  const [lon, lat] = viewport.center
  const defaultHeight = Math.max(zoomToCameraHeight(viewport.zoom, lat), preset.height)
  const height = preset.id === 'road-cruise'
    ? Math.min(defaultHeight, cruiseCameraHeight(preset))
    : defaultHeight
  void viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat, height),
    orientation: cameraOrientation(preset),
    duration: durationSec,
    complete: () => viewer.scene.requestRender(),
  })
}
