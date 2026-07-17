<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import {
  CESIUM_OSM_BUILDINGS_ASSET_ID,
  XIONGAN_3DTILES_CALIBRATION,
  resolveCesiumCameraPreset,
} from '../../constants/mapDefaults'
import { fetchMapConfig } from '../../api/mapConfig'
import type { MapRuntimeConfig } from '../../types/mapConfig'
import { useAppMapView } from '../../composables/useAppMapView'

const mapView = useAppMapView()
const containerRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

let viewer: Cesium.Viewer | null = null
let localTileset: Cesium.Cesium3DTileset | null = null

const DEFAULT_TILESET_SSE = 24
const MOVING_TILESET_SSE = 36
const CRUISE_TILESET_SSE = 28
const DEFAULT_CACHE_BYTES = 384 * 1024 * 1024
const DEFAULT_CACHE_OVERFLOW_BYTES = 256 * 1024 * 1024
const CRUISE_CACHE_BYTES = 512 * 1024 * 1024
const CRUISE_CACHE_OVERFLOW_BYTES = 256 * 1024 * 1024
const TIANDITU_ERROR_LIMIT = 3
const LOCAL_TILESET_ERROR_LIMIT = 3

let defaultMinimumZoomDistance = 1
let defaultMaximumZoomDistance = Number.POSITIVE_INFINITY
let defaultFoveatedScreenSpaceError = true
let defaultFoveatedTimeDelay = 0.2

function isCruisePreset(): boolean {
  return mapView.cameraPreset.value === 'road-cruise'
}

function applyCruiseRenderingProfile(currentViewer: Cesium.Viewer): void {
  const cruise = isCruisePreset()
  const controller = currentViewer.scene.screenSpaceCameraController
  const preset = resolveCesiumCameraPreset(mapView.cameraPreset.value)

  controller.minimumZoomDistance = cruise
    ? (preset.minimumZoomDistance ?? 150)
    : defaultMinimumZoomDistance
  controller.maximumZoomDistance = cruise
    ? (preset.maximumZoomDistance ?? 1600)
    : defaultMaximumZoomDistance

  currentViewer.scene.fog.enabled = cruise
  if (cruise) {
    currentViewer.scene.fog.density = 0.00045
    currentViewer.scene.fog.minimumBrightness = 0.12
  }

  if (localTileset) {
    localTileset.maximumScreenSpaceError = cruise ? CRUISE_TILESET_SSE : DEFAULT_TILESET_SSE
    localTileset.cacheBytes = cruise ? CRUISE_CACHE_BYTES : DEFAULT_CACHE_BYTES
    localTileset.maximumCacheOverflowBytes = cruise
      ? CRUISE_CACHE_OVERFLOW_BYTES
      : DEFAULT_CACHE_OVERFLOW_BYTES
    localTileset.dynamicScreenSpaceErrorFactor = cruise ? 12 : 4
    localTileset.foveatedScreenSpaceError = cruise ? true : defaultFoveatedScreenSpaceError
    localTileset.foveatedTimeDelay = cruise ? 0.35 : defaultFoveatedTimeDelay
  }

  requestSceneRender(currentViewer)
}

const stopPresetWatch = watch(
  () => mapView.cameraPreset.value,
  () => {
    if (viewer) {
      applyCruiseRenderingProfile(viewer)
    }
  },
)

function handleCameraMoveStart(): void {
  if (isCruisePreset() && localTileset) {
    localTileset.maximumScreenSpaceError = MOVING_TILESET_SSE
  }
}

function handleCameraMoveEnd(): void {
  if (viewer && isCruisePreset() && localTileset) {
    localTileset.maximumScreenSpaceError = CRUISE_TILESET_SSE
    requestSceneRender(viewer)
  }
}

function requestSceneRender(currentViewer: Cesium.Viewer): void {
  currentViewer.scene.requestRender()
}

function optimizeCesiumViewer(currentViewer: Cesium.Viewer): void {
  const { scene } = currentViewer
  currentViewer.resolutionScale = window.devicePixelRatio > 1 ? 0.82 : 1
  currentViewer.targetFrameRate = 45
  scene.requestRenderMode = true
  scene.maximumRenderTimeChange = 1 / 15
  scene.highDynamicRange = false
  scene.fog.enabled = false
  scene.globe.enableLighting = false
  scene.globe.baseColor = Cesium.Color.fromCssColorString('#0d1b2a')
  if (scene.skyAtmosphere) scene.skyAtmosphere.show = false
  if (scene.sun) scene.sun.show = false
  if (scene.moon) scene.moon.show = false
  if (scene.skyBox) scene.skyBox.show = false
  currentViewer.camera.percentageChanged = 0.02
  currentViewer.camera.changed.addEventListener(() => requestSceneRender(currentViewer))
}

function applyTilesetCalibration(tileset: Cesium.Cesium3DTileset): void {
  const c = XIONGAN_3DTILES_CALIBRATION
  if (c.eastMeters === 0 && c.northMeters === 0 && c.upMeters === 0
    && c.headingDegrees === 0 && c.scale === 1) return

  const center = tileset.boundingSphere.center
  const enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(center)
  const fixedToEnu = Cesium.Matrix4.inverse(enuToFixed, new Cesium.Matrix4())
  const localTranslation = Cesium.Matrix4.fromTranslation(
    new Cesium.Cartesian3(c.eastMeters, c.northMeters, c.upMeters),
  )
  const localRotation = Cesium.Matrix4.fromRotationTranslation(
    Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(c.headingDegrees)),
  )
  const localScale = Cesium.Matrix4.fromUniformScale(c.scale)
  const localTransform = Cesium.Matrix4.multiply(
    localTranslation,
    Cesium.Matrix4.multiply(localRotation, localScale, new Cesium.Matrix4()),
    new Cesium.Matrix4(),
  )
  tileset.modelMatrix = Cesium.Matrix4.multiply(
    enuToFixed,
    Cesium.Matrix4.multiply(
      Cesium.Matrix4.multiply(localTransform, fixedToEnu, new Cesium.Matrix4()),
      tileset.modelMatrix,
      new Cesium.Matrix4(),
    ),
    new Cesium.Matrix4(),
  )
}

async function assertLocalTilesetAvailable(tilesetUrl: string): Promise<void> {
  const response = await fetch(tilesetUrl)
  if (!response.ok) throw new Error(`tileset.json returned HTTP ${response.status}`)

  const manifest = await response.json() as {
    root?: { content?: { uri?: string }, children?: Array<{ content?: { uri?: string } }> }
  }
  const sampleUri = manifest.root?.content?.uri
    ?? manifest.root?.children?.find((child) => child.content?.uri)?.content?.uri
  if (!sampleUri) return

  const sampleUrl = new URL(sampleUri, new URL(tilesetUrl, window.location.href)).toString()
  const sampleResponse = await fetch(sampleUrl, { method: 'HEAD' })
  if (!sampleResponse.ok) {
    throw new Error(`referenced tile returned HTTP ${sampleResponse.status}: ${sampleUri}`)
  }
}

async function loadLocalBuildings(
  currentViewer: Cesium.Viewer,
  tilesetUrl: string,
  ionToken: string,
): Promise<void> {
  await assertLocalTilesetAvailable(tilesetUrl)
  const tileset = await Cesium.Cesium3DTileset.fromUrl(tilesetUrl, {
    maximumScreenSpaceError: DEFAULT_TILESET_SSE,
    cacheBytes: DEFAULT_CACHE_BYTES,
    maximumCacheOverflowBytes: DEFAULT_CACHE_OVERFLOW_BYTES,
    dynamicScreenSpaceError: true,
    dynamicScreenSpaceErrorDensity: 0.0028,
    dynamicScreenSpaceErrorFactor: 4,
    cullWithChildrenBounds: true,
    preloadWhenHidden: false,
    skipLevelOfDetail: false,
  })
  let tileErrorCount = 0
  let tilesetDisabled = false
  tileset.tileFailed.addEventListener((event) => {
    tileErrorCount += 1
    if (!tilesetDisabled && tileErrorCount >= LOCAL_TILESET_ERROR_LIMIT) {
      tilesetDisabled = true
      if (localTileset === tileset) localTileset = null
      currentViewer.scene.primitives.remove(tileset)
      if (ionToken) {
        void loadOsmBuildings(currentViewer).catch((cause) => {
          console.warn('[CesiumMap] OSM Buildings fallback failed; imagery remains available.', cause)
        })
      }
      requestSceneRender(currentViewer)
    }
    console.warn('[CesiumMap] 3D Tiles tile request failed; basemap remains available.', {
      message: event.message,
      url: event.url,
      tileErrorCount,
    })
  })
  applyTilesetCalibration(tileset)
  if (tilesetDisabled) return
  localTileset = tileset
  defaultFoveatedScreenSpaceError = tileset.foveatedScreenSpaceError
  defaultFoveatedTimeDelay = tileset.foveatedTimeDelay
  currentViewer.scene.primitives.add(tileset)
  applyCruiseRenderingProfile(currentViewer)
  requestSceneRender(currentViewer)
}

async function loadOsmBuildings(currentViewer: Cesium.Viewer): Promise<void> {
  const tileset = await Cesium.Cesium3DTileset.fromIonAssetId(CESIUM_OSM_BUILDINGS_ASSET_ID)
  currentViewer.scene.primitives.add(tileset)
  requestSceneRender(currentViewer)
}

function addTiandituLayers(
  currentViewer: Cesium.Viewer,
  proxyBaseUrl: string,
  ionToken: string,
): void {
  let totalErrors = 0
  let fallbackStarted = false
  let imgLayer: Cesium.ImageryLayer | null = null
  let ciaLayer: Cesium.ImageryLayer | null = null

  const switchFromTianditu = async () => {
    if (fallbackStarted || totalErrors < TIANDITU_ERROR_LIMIT) return
    fallbackStarted = true

    let fallbackLayer: Cesium.ImageryLayer
    try {
      fallbackLayer = await createCesiumIonBaseLayer(ionToken)
      console.warn('[CesiumMap] Tianditu unavailable; switched to Cesium Ion imagery.')
    } catch (ionCause) {
      console.warn('[CesiumMap] Cesium Ion imagery unavailable; using offline basemap.', ionCause)
      fallbackLayer = await createOfflineBaseLayer()
    }

    currentViewer.imageryLayers.add(fallbackLayer, 0)
    if (imgLayer && currentViewer.imageryLayers.contains(imgLayer)) {
      currentViewer.imageryLayers.remove(imgLayer, true)
    }
    if (ciaLayer && currentViewer.imageryLayers.contains(ciaLayer)) {
      currentViewer.imageryLayers.remove(ciaLayer, true)
    }
    requestSceneRender(currentViewer)
  }

  const makeProvider = (layer: 'img' | 'cia') => {
    const provider = new Cesium.WebMapTileServiceImageryProvider({
      url: `${proxyBaseUrl}/${layer}/wmts`,
      layer,
      style: 'default',
      format: 'tiles',
      tileMatrixSetID: 'w',
      minimumLevel: 0,
      maximumLevel: 18,
    })

    provider.errorEvent.addEventListener((providerError) => {
      totalErrors += 1
      providerError.retry = totalErrors < TIANDITU_ERROR_LIMIT
      queueMicrotask(() => { void switchFromTianditu() })
      console.warn(`[CesiumMap] Tianditu ${layer} request failed`, {
        message: providerError.message,
        timesRetried: providerError.timesRetried,
        totalErrors,
      })
    })
    return provider
  }

  imgLayer = currentViewer.imageryLayers.addImageryProvider(makeProvider('img'))
  imgLayer.show = true
  imgLayer.alpha = 1
  imgLayer.brightness = 1
  imgLayer.contrast = 1
  imgLayer.saturation = 1

  ciaLayer = currentViewer.imageryLayers.addImageryProvider(makeProvider('cia'))
  ciaLayer.show = true
  ciaLayer.alpha = 1
  ciaLayer.brightness = 1
  ciaLayer.contrast = 1
  ciaLayer.saturation = 1

  requestSceneRender(currentViewer)
}

async function createCesiumIonBaseLayer(ionToken: string): Promise<Cesium.ImageryLayer> {
  if (!ionToken) throw new Error('Cesium Ion token is empty')
  Cesium.Ion.defaultAccessToken = ionToken
  const provider = await Cesium.createWorldImageryAsync()
  return new Cesium.ImageryLayer(provider)
}

async function createOfflineBaseLayer(): Promise<Cesium.ImageryLayer> {
  const provider = await Cesium.TileMapServiceImageryProvider.fromUrl(
    Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
  )
  return new Cesium.ImageryLayer(provider)
}

async function initViewer() {
  if (!containerRef.value) return

  let mapConfig: MapRuntimeConfig
  try {
    mapConfig = await fetchMapConfig()
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : '未知错误'
    error.value = `无法加载地图配置，请确认后端已启动：${message}`
    loading.value = false
    return
  }

  const ionToken = mapConfig.cesiumIonToken
  const tiandituEnabled = mapConfig.tiandituEnabled

  if (ionToken) Cesium.Ion.defaultAccessToken = ionToken

  let baseLayer: Cesium.ImageryLayer | false = false
  if (!tiandituEnabled) {
    try {
      baseLayer = ionToken
        ? await createCesiumIonBaseLayer(ionToken)
        : await createOfflineBaseLayer()
    } catch (cause) {
      console.warn('[CesiumMap] Online imagery unavailable; using offline basemap.', cause)
      try {
        baseLayer = await createOfflineBaseLayer()
      } catch {
        baseLayer = false
      }
    }
  }

  viewer = new Cesium.Viewer(containerRef.value, {
    animation: false,
    timeline: false,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false,
    shadows: false,
    shouldAnimate: false,
    useBrowserRecommendedResolution: true,
    baseLayer: tiandituEnabled ? false : baseLayer,
    ...(ionToken ? { terrain: Cesium.Terrain.fromWorldTerrain() } : {}),
  })

  optimizeCesiumViewer(viewer)
  defaultMinimumZoomDistance = viewer.scene.screenSpaceCameraController.minimumZoomDistance
  defaultMaximumZoomDistance = viewer.scene.screenSpaceCameraController.maximumZoomDistance
  viewer.camera.moveStart.addEventListener(handleCameraMoveStart)
  viewer.camera.moveEnd.addEventListener(handleCameraMoveEnd)
  applyCruiseRenderingProfile(viewer)
  if (tiandituEnabled) {
    addTiandituLayers(viewer, mapConfig.tiandituProxyBaseUrl, ionToken)
  }

  viewer.scene.globe.depthTestAgainstTerrain = Boolean(ionToken)

  try {
    await loadLocalBuildings(viewer, mapConfig.xiongan3dTilesUrl, ionToken)
  } catch (localCause) {
    const msg = localCause instanceof Error ? localCause.message : '未知错误'
    if (!ionToken) {
      console.warn(`[CesiumMap] Local buildings unavailable; imagery basemap remains available: ${msg}`)
    } else {
      try {
        await loadOsmBuildings(viewer)
        console.warn(`[CesiumMap] Local buildings unavailable; using OSM Buildings: ${msg}`)
      } catch (osmCause) {
        const osmMsg = osmCause instanceof Error ? osmCause.message : '未知错误'
        console.warn(
          `[CesiumMap] Buildings disabled; imagery basemap remains available. Local: ${msg}; OSM: ${osmMsg}`,
        )
      }
    }
  }

  mapView.registerCesium(viewer)
  loading.value = false
  requestSceneRender(viewer)
}

onMounted(() => { void initViewer() })

onUnmounted(() => {
  stopPresetWatch()
  mapView.unregisterCesium()
  if (viewer) {
    viewer.camera.moveStart.removeEventListener(handleCameraMoveStart)
    viewer.camera.moveEnd.removeEventListener(handleCameraMoveEnd)
  }
  viewer?.destroy()
  viewer = null
  localTileset = null
})
</script>

<template>
  <div class="app-cesium-map">
    <div ref="containerRef" class="app-cesium-map__canvas" />
    <div v-if="loading" class="app-cesium-map__overlay">正在加载三维地图…</div>
    <div
      v-else-if="error"
      class="app-cesium-map__overlay app-cesium-map__overlay--error"
    >
      {{ error }}
    </div>
  </div>
</template>

<style scoped>
.app-cesium-map {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
}

.app-cesium-map__canvas {
  width: 100%;
  height: 100%;
}

.app-cesium-map__canvas :deep(.cesium-viewer-bottom) {
  display: none;
}

.app-cesium-map__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(2, 10, 24, 0.72);
  color: #9edfff;
  font-size: 14px;
  text-align: center;
  pointer-events: none;
}

.app-cesium-map__overlay--error {
  color: #ffb4b4;
}
</style>
