<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import {
  CESIUM_OSM_BUILDINGS_ASSET_ID,
  XIONGAN_3DTILES_CALIBRATION,
  XIONGAN_3DTILES_DEFAULT_URL,
  XIONGAN_ROAD_VISUAL_SEGMENTS,
  type RoadVisualLevel,
} from '../../constants/mapDefaults'
import { useAppMapView } from '../../composables/useAppMapView'

const mapView = useAppMapView()
const containerRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

let viewer: Cesium.Viewer | null = null
let roadEntities: Cesium.Entity[] = []

const ROAD_STYLE: Record<RoadVisualLevel, {
  baseWidth: number
  glowWidth: number
  glowPower: number
  baseColor: Cesium.Color
  glowColor: Cesium.Color
}> = {
  arterial: {
    baseWidth: 6,
    glowWidth: 2.5,
    glowPower: 0.22,
    baseColor: Cesium.Color.fromCssColorString('#07345d').withAlpha(0.82),
    glowColor: Cesium.Color.fromCssColorString('#20e8ff').withAlpha(0.92),
  },
  secondary: {
    baseWidth: 4,
    glowWidth: 1.8,
    glowPower: 0.18,
    baseColor: Cesium.Color.fromCssColorString('#062744').withAlpha(0.72),
    glowColor: Cesium.Color.fromCssColorString('#56f0ff').withAlpha(0.80),
  },
  connector: {
    baseWidth: 2.5,
    glowWidth: 1.2,
    glowPower: 0.14,
    baseColor: Cesium.Color.fromCssColorString('#041c32').withAlpha(0.62),
    glowColor: Cesium.Color.fromCssColorString('#8ff8ff').withAlpha(0.66),
  },
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

  // 影像未加载时显示深色背景，避免默认蓝色
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
  currentViewer.scene.primitives.add(tileset)
  requestSceneRender(currentViewer)
}

async function loadOsmBuildings(currentViewer: Cesium.Viewer): Promise<void> {
  const tileset = await Cesium.Cesium3DTileset.fromIonAssetId(CESIUM_OSM_BUILDINGS_ASSET_ID)
  currentViewer.scene.primitives.add(tileset)
  requestSceneRender(currentViewer)
}

/**
 * 天地图影像层通过 addImageryProvider 叠加，不作为 baseLayer 传入 Viewer。
 * 原因：new WebMapTileServiceImageryProvider() 是同步构造，provider 内部异步就绪，
 * 若直接传给 Viewer baseLayer，Cesium 在 provider 未就绪时渲染 globe.baseColor（蓝色）。
 * 叠加方式不存在此问题，Cesium 会在 provider 就绪后自动开始请求瓦片。
 */
function addTiandituLayers(currentViewer: Cesium.Viewer, token: string): void {
  const makeProvider = (layer: 'img' | 'cia') => new Cesium.WebMapTileServiceImageryProvider({
    url: `https://t{s}.tianditu.gov.cn/${layer}_w/wmts?tk=${encodeURIComponent(token)}`,
    layer,
    style: 'default',
    format: 'tiles',
    tileMatrixSetID: 'w',
    subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
    minimumLevel: 1,
    maximumLevel: 18,
  })

  const imgLayer = currentViewer.imageryLayers.addImageryProvider(makeProvider('img'), 0)
  imgLayer.errorEvent.addEventListener((e) => {
    console.error('[CesiumMap] 天地图影像瓦片失败:', e.message)
  })

  const ciaLayer = currentViewer.imageryLayers.addImageryProvider(makeProvider('cia'))
  ciaLayer.errorEvent.addEventListener((e) => {
    console.error('[CesiumMap] 天地图注记瓦片失败:', e.message)
  })
}

async function createOfflineBaseLayer(): Promise<Cesium.ImageryLayer> {
  const provider = await Cesium.TileMapServiceImageryProvider.fromUrl(
    Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
  )
  return new Cesium.ImageryLayer(provider)
}

function addRoadVisualLayer(currentViewer: Cesium.Viewer): void {
  roadEntities = XIONGAN_ROAD_VISUAL_SEGMENTS.flatMap((segment) => {
    const style = ROAD_STYLE[segment.level]
    const positions = segment.coordinates.map(([lon, lat]) =>
      Cesium.Cartesian3.fromDegrees(lon, lat),
    )
    const base = currentViewer.entities.add({
      id: `road-base-${segment.id}`,
      polyline: {
        positions,
        width: style.baseWidth,
        material: style.baseColor,
        clampToGround: true,
        classificationType: Cesium.ClassificationType.TERRAIN,
      },
    })
    const glow = currentViewer.entities.add({
      id: `road-glow-${segment.id}`,
      polyline: {
        positions,
        width: style.glowWidth,
        material: new Cesium.PolylineGlowMaterialProperty({
          glowPower: style.glowPower,
          taperPower: 0.5,
          color: style.glowColor,
        }),
        clampToGround: true,
        classificationType: Cesium.ClassificationType.TERRAIN,
      },
    })
    return [base, glow]
  })

  const beaconPoints: Array<[number, number]> = [
    [115.958, 38.985], [115.981, 38.985], [116.005, 38.985],
    [115.970, 39.001], [115.993, 39.001],
    [115.970, 38.969], [115.993, 38.969],
  ]
  beaconPoints.forEach(([lon, lat], i) => {
    roadEntities.push(currentViewer.entities.add({
      id: `road-beacon-${i}`,
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 2),
      ellipse: {
        semiMajorAxis: 12,
        semiMinorAxis: 12,
        height: 1,
        material: Cesium.Color.fromCssColorString('#21e6ff').withAlpha(0.20),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString('#21e6ff').withAlpha(0.78),
        outlineWidth: 1,
      },
    }))
  })

  requestSceneRender(currentViewer)
}

function clearRoadVisualLayer(currentViewer: Cesium.Viewer): void {
  roadEntities.forEach((e) => currentViewer.entities.remove(e))
  roadEntities = []
}

async function initViewer() {
  const ionToken = import.meta.env.VITE_CESIUM_ION_TOKEN?.trim()
  const tiandituToken = import.meta.env.VITE_TIANDITU_TOKEN?.trim()
  if (!containerRef.value) return

  if (ionToken) Cesium.Ion.defaultAccessToken = ionToken

  // 没有天地图 token 时，用离线底图或 Bing 初始化
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
    // 有天地图时传 false，让 imageryLayers 完全由我们管理
    baseLayer: tiandituToken ? false : baseLayer,
    ...(ionToken ? { terrain: Cesium.Terrain.fromWorldTerrain() } : {}),
  })

  optimizeCesiumViewer(viewer)

  if (tiandituToken) {
    // 天地图通过 addImageryProvider 叠加，避免 baseLayer 同步初始化问题
    addTiandituLayers(viewer, tiandituToken)
  }

  viewer.scene.globe.depthTestAgainstTerrain = Boolean(ionToken)
  addRoadVisualLayer(viewer)

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
  mapView.unregisterCesium()
  if (viewer) clearRoadVisualLayer(viewer)
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
