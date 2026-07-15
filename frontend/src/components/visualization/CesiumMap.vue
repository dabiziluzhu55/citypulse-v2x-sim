<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import {
  CESIUM_OSM_BUILDINGS_ASSET_ID,
  XIONGAN_3DTILES_CALIBRATION,
  XIONGAN_3DTILES_DEFAULT_URL,
} from '../../constants/mapDefaults'
import { useAppMapView } from '../../composables/useAppMapView'

const mapView = useAppMapView()
const containerRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

let viewer: Cesium.Viewer | null = null

function applyTilesetCalibration(tileset: Cesium.Cesium3DTileset): void {
  const calibration = XIONGAN_3DTILES_CALIBRATION
  const unchanged = calibration.eastMeters === 0
    && calibration.northMeters === 0
    && calibration.upMeters === 0
    && calibration.headingDegrees === 0
    && calibration.scale === 1

  if (unchanged) {
    return
  }

  const center = tileset.boundingSphere.center
  const enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(center)
  const fixedToEnu = Cesium.Matrix4.inverse(enuToFixed, new Cesium.Matrix4())
  const localTranslation = Cesium.Matrix4.fromTranslation(
    new Cesium.Cartesian3(
      calibration.eastMeters,
      calibration.northMeters,
      calibration.upMeters,
    ),
  )
  const localRotation = Cesium.Matrix4.fromRotationTranslation(
    Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(calibration.headingDegrees)),
  )
  const localScale = Cesium.Matrix4.fromUniformScale(calibration.scale)
  const localTransform = Cesium.Matrix4.multiply(
    localTranslation,
    Cesium.Matrix4.multiply(localRotation, localScale, new Cesium.Matrix4()),
    new Cesium.Matrix4(),
  )
  const fixedTransform = Cesium.Matrix4.multiply(
    enuToFixed,
    Cesium.Matrix4.multiply(localTransform, fixedToEnu, new Cesium.Matrix4()),
    new Cesium.Matrix4(),
  )

  tileset.modelMatrix = Cesium.Matrix4.multiply(
    fixedTransform,
    tileset.modelMatrix,
    new Cesium.Matrix4(),
  )
}

async function loadLocalBuildings(currentViewer: Cesium.Viewer): Promise<void> {
  const configuredUrl = import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim()
  const tilesetUrl = configuredUrl || XIONGAN_3DTILES_DEFAULT_URL
  const tileset = await Cesium.Cesium3DTileset.fromUrl(tilesetUrl, {
    maximumScreenSpaceError: 20,
    cacheBytes: 256 * 1024 * 1024,
    maximumCacheOverflowBytes: 128 * 1024 * 1024,
    dynamicScreenSpaceError: true,
    cullWithChildrenBounds: true,
    preloadWhenHidden: false,
  })

  tileset.tileFailed.addEventListener((event) => {
    const details = event.message || event.url || '未知子瓦片错误'
    error.value = `部分 3D Tiles 加载失败：${details}`
  })

  applyTilesetCalibration(tileset)
  currentViewer.scene.primitives.add(tileset)
}

async function loadOsmBuildings(currentViewer: Cesium.Viewer): Promise<void> {
  const tileset = await Cesium.Cesium3DTileset.fromIonAssetId(CESIUM_OSM_BUILDINGS_ASSET_ID)
  currentViewer.scene.primitives.add(tileset)
}

async function createOfflineBaseLayer(): Promise<Cesium.ImageryLayer> {
  const imageryProvider = await Cesium.TileMapServiceImageryProvider.fromUrl(
    Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
  )
  return new Cesium.ImageryLayer(imageryProvider)
}

function createTiandituProvider(
  layer: 'img' | 'cia',
  token: string,
): Cesium.WebMapTileServiceImageryProvider {
  return new Cesium.WebMapTileServiceImageryProvider({
    url: `https://t{s}.tianditu.gov.cn/${layer}_w/wmts?tk=${encodeURIComponent(token)}`,
    layer,
    style: 'default',
    format: 'tiles',
    tileMatrixSetID: 'w',
    subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
    maximumLevel: 18,
  })
}

function addTiandituLayers(currentViewer: Cesium.Viewer, token: string): void {
  const imageryLayer = currentViewer.imageryLayers.addImageryProvider(
    createTiandituProvider('img', token),
  )
  imageryLayer.errorEvent.addEventListener((tileProviderError) => {
    console.error(`天地图影像瓦片加载失败：${tileProviderError.message}`)
  })

  const annotationLayer = currentViewer.imageryLayers.addImageryProvider(
    createTiandituProvider('cia', token),
  )
  annotationLayer.errorEvent.addEventListener((tileProviderError) => {
    console.error(`天地图中文注记加载失败：${tileProviderError.message}`)
  })
}

async function initViewer() {
  const ionToken = import.meta.env.VITE_CESIUM_ION_TOKEN?.trim()
  const tiandituToken = import.meta.env.VITE_TIANDITU_TOKEN?.trim()
  if (!containerRef.value) {
    return
  }

  if (ionToken) {
    Cesium.Ion.defaultAccessToken = ionToken
  }

  let baseLayer: Cesium.ImageryLayer | false = false
  try {
    baseLayer = await createOfflineBaseLayer()
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : '未知错误'
    console.error(`Cesium 底图加载失败：${message}`)
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
    baseLayer,
    ...(ionToken ? { terrain: Cesium.Terrain.fromWorldTerrain() } : {}),
  })

  if (tiandituToken) {
    addTiandituLayers(viewer, tiandituToken)
  } else {
    console.warn('未配置 VITE_TIANDITU_TOKEN，当前使用离线 Natural Earth II 底图。')
  }

  viewer.scene.globe.depthTestAgainstTerrain = Boolean(ionToken)

  try {
    await loadLocalBuildings(viewer)
  } catch (localCause) {
    const localMessage = localCause instanceof Error ? localCause.message : '未知错误'

    if (!ionToken) {
      error.value = `本地彩色建筑加载失败：${localMessage}。如需 OSM Buildings 回退，请配置 Cesium ion 令牌。`
    } else {
      try {
        await loadOsmBuildings(viewer)
        console.warn(`本地彩色建筑加载失败，已回退到 OSM Buildings：${localMessage}`)
      } catch (osmCause) {
        const osmMessage = osmCause instanceof Error ? osmCause.message : '未知错误'
        error.value = `本地彩色建筑加载失败：${localMessage}；OSM Buildings 回退失败：${osmMessage}`
      }
    }
  }

  mapView.registerCesium(viewer)
  loading.value = false
}

onMounted(() => {
  void initViewer()
})

onUnmounted(() => {
  mapView.unregisterCesium()
  viewer?.destroy()
  viewer = null
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
