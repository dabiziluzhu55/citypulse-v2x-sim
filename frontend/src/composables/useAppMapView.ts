import { inject, onScopeDispose, provide, ref, shallowRef } from 'vue'
import type Map from 'ol/Map'
import type { Viewer } from 'cesium'
import {
  DEFAULT_CESIUM_CAMERA_PRESET_ID,
  DEFAULT_MAP_CENTER,
  DEFAULT_MAP_ZOOM,
  resolveCesiumCameraPreset,
  resolveTemplateMapViewport,
  XIONGAN_MAP_BOUNDS,
} from '../constants/mapDefaults'
import type { AppMapMode, AppMapView, CesiumCameraPresetId, MapDimension, ThreeMapController } from '../types/map'
import { appMapViewKey } from '../types/map'
import {
  applyCesiumViewport,
  applyOlViewport,
  type ApplyViewportOptions,
  type StoredMapViewport,
} from '../utils/mapViewportSync'
import { wgs84ToBd09 } from '../mapv/sceneCoordinates'

export function provideAppMapView() {
  const mode = ref<AppMapMode>('explore')
  const dimension = ref<MapDimension>('2d')
  const cameraPreset = ref<CesiumCameraPresetId>(DEFAULT_CESIUM_CAMERA_PRESET_ID)
  const anchorId = ref<string | null>(null)
  const viewport = ref<StoredMapViewport>({
    kind: 'center',
    center: DEFAULT_MAP_CENTER,
    zoom: DEFAULT_MAP_ZOOM,
  })
  const mapRef = shallowRef<Map | null>(null)
  const threeMapRef = shallowRef<ThreeMapController | null>(null)
  const cesiumRef = shallowRef<Viewer | null>(null)

  function setDimension(next: MapDimension) {
    dimension.value = next
    applyViewport({ duration: 0 })
  }

  function setCameraPreset(next: CesiumCameraPresetId) {
    cameraPreset.value = next
    applyViewport()
  }

  function applyViewport(options: ApplyViewportOptions = {}) {
    const map = mapRef.value
    if (map) {
      applyOlViewport(map, viewport.value, options)
    }

    const threeMap = threeMapRef.value
    if (threeMap) {
      if (viewport.value.kind === 'bounds') {
        const [minLon, minLat, maxLon, maxLat] = viewport.value.bounds
        const [bdMinLon, bdMinLat] = wgs84ToBd09(minLon, minLat)
        const [bdMaxLon, bdMaxLat] = wgs84ToBd09(maxLon, maxLat)
        threeMap.setViewport(
          [[bdMinLon, bdMinLat, 0], [bdMaxLon, bdMaxLat, 0]],
          { range: 2200 },
        )
      } else {
        const preset = resolveCesiumCameraPreset(cameraPreset.value)
        const [bdLon, bdLat] = wgs84ToBd09(viewport.value.center[0], viewport.value.center[1])
        threeMap.flyTo(
          [bdLon, bdLat, 0],
          {
            heading: preset.headingDegrees,
            pitch: Math.abs(preset.pitchDegrees),
            range: preset.height,
            duration: options.duration ?? 0,
            complete: () => undefined,
          },
        )
      }
    }

    const viewer = cesiumRef.value
    if (viewer) {
      applyCesiumViewport(viewer, viewport.value, {
        ...options,
        cameraPreset: resolveCesiumCameraPreset(cameraPreset.value),
      })
    }
  }

  function registerMap(map: Map) {
    mapRef.value = map
    applyViewport({ duration: 0 })
  }

  function unregisterMap() {
    mapRef.value = null
  }

  function registerThreeMap(map: ThreeMapController) {
    threeMapRef.value = map
    applyViewport({ duration: 0 })
  }

  function unregisterThreeMap() {
    threeMapRef.value = null
  }

  function registerCesium(viewer: Viewer) {
    cesiumRef.value = viewer
    applyViewport({ duration: 0 })
  }

  function unregisterCesium() {
    cesiumRef.value = null
  }

  function setViewport(next: StoredMapViewport, options: ApplyViewportOptions = {}) {
    viewport.value = next
    applyViewport(options)
  }

  function flyTo(center: [number, number], zoom: number, nextAnchorId?: string) {
    if (nextAnchorId && anchorId.value === nextAnchorId) {
      return
    }

    mode.value = nextAnchorId ? 'anchored' : mode.value
    if (nextAnchorId) {
      anchorId.value = nextAnchorId
    }
    setViewport({ kind: 'center', center, zoom })
  }

  function fitBounds(bounds: [number, number, number, number], nextAnchorId?: string) {
    if (nextAnchorId && anchorId.value === nextAnchorId) {
      return
    }

    mode.value = nextAnchorId ? 'anchored' : mode.value
    if (nextAnchorId) {
      anchorId.value = nextAnchorId
    }
    setViewport({ kind: 'bounds', bounds })
  }

  function flyToTemplate(templateId: string) {
    if (!templateId) {
      resetToDefault()
      return
    }

    const templateViewport = resolveTemplateMapViewport(templateId)
    if (templateViewport.bounds) {
      fitBounds(templateViewport.bounds, `template:${templateId}`)
      return
    }

    flyTo(
      templateViewport.center,
      templateViewport.zoom ?? DEFAULT_MAP_ZOOM,
      `template:${templateId}`,
    )
  }

  function flyToScenario(scenarioId: string, templateId?: string) {
    if (!scenarioId) {
      return
    }

    if (templateId) {
      flyToTemplate(templateId)
      anchorId.value = `scenario:${scenarioId}`
      return
    }

    flyTo(DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM, `scenario:${scenarioId}`)
  }

  function resetToDefault() {
    mode.value = 'explore'
    anchorId.value = null
    fitBounds(XIONGAN_MAP_BOUNDS)
  }

  const api: AppMapView = {
    mode,
    dimension,
    cameraPreset,
    anchorId,
    viewport,
    setDimension,
    setCameraPreset,
    registerMap,
    unregisterMap,
    registerThreeMap,
    unregisterThreeMap,
    registerCesium,
    unregisterCesium,
    flyTo,
    fitBounds,
    flyToTemplate,
    flyToScenario,
    resetToDefault,
  }

  provide(appMapViewKey, api)
  return api
}

export function useAppMapView() {
  const mapView = inject(appMapViewKey)
  if (!mapView) {
    throw new Error('useAppMapView must be used within App.vue provideAppMapView')
  }
  return mapView
}

export function useOptionalAppMapView() {
  return inject(appMapViewKey, null)
}

export function bindMapInstance(mapView: AppMapView, map: Map) {
  mapView.registerMap(map)
  onScopeDispose(() => {
    mapView.unregisterMap()
  })
}

export function bindThreeMapInstance(mapView: AppMapView, map: ThreeMapController) {
  mapView.registerThreeMap(map)
  onScopeDispose(() => {
    mapView.unregisterThreeMap()
  })
}

export function bindCesiumInstance(mapView: AppMapView, viewer: Viewer) {
  mapView.registerCesium(viewer)
  onScopeDispose(() => {
    mapView.unregisterCesium()
  })
}
