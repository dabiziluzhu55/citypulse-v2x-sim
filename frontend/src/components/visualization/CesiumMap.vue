<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import {
  CESIUM_OSM_BUILDINGS_ASSET_ID,
  XIONGAN_3DTILES_CALIBRATION,
  XIONGAN_3DTILES_DEFAULT_URL,
  resolveCesiumCameraPreset,
} from '../../constants/mapDefaults'
import { useAppMapView } from '../../composables/useAppMapView'

const mapView = useAppMapView()
const containerRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

let viewer: Cesium.Viewer | null = null
let localTileset: Cesium.Cesium3DTileset | null = null

const DEFAULT_TILESET_SSE = 16
const MOVING_TILESET_SSE = 28
const CRUISE_TILESET_SSE = 22
const DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
const DEFAULT_CACHE_OVERFLOW_BYTES = 128 * 1024 * 1024
const CRUISE_CACHE_BYTES = 384 * 1024 * 1024
const CRUISE_CACHE_OVERFLOW_BYTES = 192 * 1024 * 1024

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

async function loadLocalBuildings(currentViewer: Cesium.Viewer): Promise<void> {
  const tilesetUrl = import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim() || XIONGAN_3DTILES_DEFAULT_URL
  const tileset = await Cesium.Cesium3DTileset.fromUrl(tilesetUrl, {
    maximumScreenSpaceError: 16,
    cacheBytes: 256 * 1024 * 1024,
    maximumCacheOverflowBytes: 128 * 1024 * 1024,
    dynamicScreenSpaceError: true,
    dynamicScreenSpaceErrorDensity: 0.0028,
    dynamicScreenSpaceErrorFactor: 4,
    cullWithChildrenBounds: true,
    preloadWhenHidden: false,
    skipLevelOfDetail: false,
  })
  tileset.tileFailed.addEventListener((event) => {
    error.value = `部分 3D Tiles 加载失败：${event.message || event.url || '未知'}`
  })
  applyTilesetCalibration(tileset)
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

function addTiandituLayers(currentViewer: Cesium.Viewer, token: string): void {
  const makeProvider = (layer: 'img' | 'cia') => {
    const provider = new Cesium.WebMapTileServiceImageryProvider({
      url: `https://t{s}.tianditu.gov.cn/${layer}_w/wmts?tk=${encodeURIComponent(token)}`,
      layer,
      style: 'default',
      format: 'tiles',
      tileMatrixSetID: 'w',
      subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
      minimumLevel: 0,
      maximumLevel: 18,
    })

    provider.errorEvent.addEventListener((providerError) => {
      console.error(`[CesiumMap] 天地图 ${layer} provider 请求失败`, {
        message: providerError.message,
        timesRetried: providerError.timesRetried,
        retry: providerError.retry,
      })
    })
    return provider
  }

  const imgLayer = currentViewer.imageryLayers.addImageryProvider(makeProvider('img'), 0)
  imgLayer.show = true
  imgLayer.alpha = 1
  imgLayer.brightness = 1
  imgLayer.contrast = 1
  imgLayer.saturation = 1

  const ciaLayer = currentViewer.imageryLayers.addImageryProvider(makeProvider('cia'))
  ciaLayer.show = true
  ciaLayer.alpha = 1
  ciaLayer.brightness = 1
  ciaLayer.contrast = 1
  ciaLayer.saturation = 1

  requestSceneRender(currentViewer)
}

async function createOfflineBaseLayer(): Promise<Cesium.ImageryLayer> {
  const provider = await Cesium.TileMapServiceImageryProvider.fromUrl(
    Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
  )
  return new Cesium.ImageryLayer(provider)
}

async function initViewer() {
  const ionToken = import.meta.env.VITE_CESIUM_ION_TOKEN?.trim()
  const tiandituToken = import.meta.env.VITE_TIANDITU_TOKEN?.trim()
  if (!containerRef.value) return

  if (ionToken) Cesium.Ion.defaultAccessToken = ionToken

  let baseLayer: Cesium.ImageryLayer | false = false
  if (!tiandituToken) {
    if (!ionToken) {
      try {
        baseLayer = await createOfflineBaseLayer()
      } catch {
        baseLayer = false
      }
    }
    console.warn('[CesiumMap] 未配置 VITE_TIANDITU_TOKEN，底图使用'
      + (ionToken ? ' Bing 卫星影像' : '离线 NaturalEarthII'))
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
    baseLayer: tiandituToken ? false : baseLayer,
    ...(ionToken ? { terrain: Cesium.Terrain.fromWorldTerrain() } : {}),
  })

  optimizeCesiumViewer(viewer)
  defaultMinimumZoomDistance = viewer.scene.screenSpaceCameraController.minimumZoomDistance
  defaultMaximumZoomDistance = viewer.scene.screenSpaceCameraController.maximumZoomDistance
  viewer.camera.moveStart.addEventListener(handleCameraMoveStart)
  viewer.camera.moveEnd.addEventListener(handleCameraMoveEnd)
  applyCruiseRenderingProfile(viewer)
  if (tiandituToken) addTiandituLayers(viewer, tiandituToken)

  viewer.scene.globe.depthTestAgainstTerrain = Boolean(ionToken)

  try {
    await loadLocalBuildings(viewer)
  } catch (localCause) {
    const msg = localCause instanceof Error ? localCause.message : '未知错误'
    if (!ionToken) {
      error.value = `本地彩色建筑加载失败：${msg}。如需 OSM Buildings 回退，请配置 Cesium ion 令牌。`
    } else {
      try {
        await loadOsmBuildings(viewer)
        console.warn(`本地建筑加载失败，回退到 OSM Buildings：${msg}`)
      } catch (osmCause) {
        const osmMsg = osmCause instanceof Error ? osmCause.message : '未知错误'
        error.value = `本地建筑失败：${msg}；OSM Buildings 也失败：${osmMsg}`
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
    <div v-else-if="error" class="app-cesium-map__overlay app-cesium-map__overlay--error">
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
