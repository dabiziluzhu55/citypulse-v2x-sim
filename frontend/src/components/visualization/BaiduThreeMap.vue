<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as mapvthree from '@baidumap/mapv-three'
import { Color } from 'three'
import { useAppMapView } from '../../composables/useAppMapView'
import { useSimulationMap } from '../../composables/useSimulationMap'
import { useSimulationStore } from '../../composables/useSimulationStore'
import { useActiveIntersectionScene } from '../../composables/useActiveIntersectionScene'
import { BaiduDetailedRoadRenderer } from '../../mapv/BaiduDetailedRoadRenderer'
import { BaiduRoadNetworkRenderer } from '../../mapv/BaiduRoadNetworkRenderer'
import {
  BaiduVehicleRenderer,
  type VehicleRenderStats,
} from '../../mapv/BaiduVehicleRenderer'
import { ShowcaseGeoJsonLayers } from '../../mapv/showcaseLayers/ShowcaseGeoJsonLayers'
import { ShowcaseModelLayers } from '../../mapv/showcaseLayers/ShowcaseModelLayers'
import { RoadsideFacilityRenderer } from '../../mapv/showcaseLayers/RoadsideFacilityRenderer'
import { VegetationRenderer } from '../../mapv/showcaseLayers/VegetationRenderer'
import { MapvRealisticIntersectionLayer } from '../../mapv/realistic/MapvRealisticIntersectionLayer'
import { parseSceneFacilityManifest } from '../../mapv/showcaseLayers/sceneFacilities'
import { BAIDU_DARK_BASE_STYLE } from '../../mapv/baiduDarkStyle'
import {
  BAIDU_3D_MAX_RANGE,
  BAIDU_3D_MIN_RANGE,
  DEFAULT_CESIUM_CAMERA_HEIGHT,
} from '../../constants/mapDefaults'
import {
  DEMO_2_SOURCE_CENTER_BD09,
  placeBaiduCameraTarget,
  resolveSimulationCoordinateProjector,
  XIONGAN_SCENE_ANCHOR_BD09,
} from '../../mapv/sceneCoordinates'
import {
  roadTilesetManifestIsValid,
  roadTilesetMatchesResponse,
  type StaticRoadTilesetManifest,
} from '../../mapv/staticRoadTileset'
import type { MapGeoJsonResponse } from '../../types/map'


const containerRef = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const {
  activeIntersectionId,
  sceneStatus,
  sceneError,
  setSceneLoading,
  setSceneReady,
  setSceneError,
} = useActiveIntersectionScene()
const { geojson } = useSimulationMap(activeIntersectionId)
const { snapshot, trafficView } = useSimulationStore()

const loading = ref(true)
const error = ref<string | null>(null)
const tilesStatus = ref<'loading' | 'ready' | 'error'>('loading')
const tilesMessage = ref('正在加载百度地图 3D 建筑…')
const interacting = ref(false)
const vehicleStats = ref<VehicleRenderStats>({
  inputCount: 0,
  visibleCount: 0,
  radiusMeters: 420,
})
const showRenderDiagnostics = import.meta.env.DEV

let engine: mapvthree.Engine | null = null
let sky: mapvthree.DefaultSky | null = null
let buildingTileset: mapvthree.Default3DTiles | null = null
let roadTileset: mapvthree.Default3DTiles | null = null
let roadTilesetManifest: StaticRoadTilesetManifest | null = null
let roadTilesReady = false
let roadRenderer: BaiduRoadNetworkRenderer | BaiduDetailedRoadRenderer | null = null
let vehicleRenderer: BaiduVehicleRenderer | null = null
let showcaseGeoJsonLayers: ShowcaseGeoJsonLayers | null = null
let showcaseModelLayers: ShowcaseModelLayers | null = null
let roadsideFacilityRenderer: RoadsideFacilityRenderer | null = null
let vegetationRenderer: VegetationRenderer | null = null
let realisticIntersectionLayer: MapvRealisticIntersectionLayer | null = null
let realisticDetailReady = false
let showcaseLandmarkPromise: Promise<void> | null = null
let tilesStatusTimer: ReturnType<typeof setInterval> | null = null
let interactionEndTimer: ReturnType<typeof setTimeout> | null = null
let vegetationLoadTimer: ReturnType<typeof setTimeout> | null = null
let buildingLoadTimer: ReturnType<typeof setTimeout> | null = null
let lastEmptyVehicleWarningSequence = -25

const tilesetUrl =
  import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim()
  || '/3dtiles/xiongan-webmercator-demo_2/tileset.json'
const enableLocalTileset = import.meta.env.VITE_ENABLE_XIONGAN_3DTILES === 'true'
const enableStaticRoadTileset = import.meta.env.VITE_ENABLE_STATIC_ROAD_3DTILES !== 'false'
const roadTilesetUrl =
  import.meta.env.VITE_STATIC_ROAD_3DTILES_URL?.trim() || '/3dtiles/roads/demo_2/tileset.json'
const scenePlacement = import.meta.env.VITE_3D_SCENE_PLACEMENT?.trim()
  || 'actual'
const coordinateProjector = resolveSimulationCoordinateProjector(scenePlacement)
const sceneCenter = scenePlacement === 'xiongan-demo'
  ? XIONGAN_SCENE_ANCHOR_BD09
  : DEMO_2_SOURCE_CENTER_BD09
const baiduAk = import.meta.env.VITE_BAIDU_MAP_AK?.trim() || ''
const showBaiduBuildings = !enableLocalTileset && import.meta.env.VITE_BAIDU_BUILDINGS !== 'false'
const showBaiduRoads = !enableStaticRoadTileset && import.meta.env.VITE_BAIDU_ROADS === 'true'
const roadRendererMode = import.meta.env.VITE_BAIDU_ROAD_RENDERER?.trim() || 'detailed'
const enableShowcaseLayers = import.meta.env.VITE_ENABLE_SHOWCASE_LAYERS === 'true'
const enableJunctionMarkers = import.meta.env.VITE_ENABLE_JUNCTION_MARKERS === 'true'
const enableRoadsideFacilities = import.meta.env.VITE_ENABLE_ROADSIDE_FACILITIES === 'true'
const roadsideFacilitiesUrl = import.meta.env.VITE_SCENE_FACILITIES_URL?.trim()
  || '/showcase-data/demo_2.facilities.json'
const showcaseLandmarkUrl = import.meta.env.VITE_SHOWCASE_LANDMARK_MODEL_URL?.trim() || ''
const enableVegetation = import.meta.env.VITE_ENABLE_VEGETATION === 'true'
const vegetationManifestUrl = import.meta.env.VITE_VEGETATION_MANIFEST_URL?.trim()
  || '/showcase-data/demo_2.vegetation.json'
const vegetationModelUrl = import.meta.env.VITE_VEGETATION_MODEL_URL?.trim()
  || '/assets/plants/low-poly-plants.glb'

const BUILDING_IDLE_ERROR_TARGET = 24
const BUILDING_MOVING_ERROR_TARGET = 96
const ACTIVE_FRAME_TIME_MS = 33

function createBaiduProvider(): mapvthree.BaiduVectorTileProvider {
  return new mapvthree.BaiduVectorTileProvider({
    ak: baiduAk,
    styleJson: BAIDU_DARK_BASE_STYLE,
    displayOptions: {
      base: true,
      link: showBaiduRoads,
      building: showBaiduBuildings,
      poi: false,
      flat: true,
    },
    placeholderColor: '#0d1b2a',
  })
}

function enableCameraInteraction(): void {
  if (!engine) return
  engine.controller.enabled = true
  engine.controller.enableRotate = true
  engine.controller.enableZoom = true
  engine.controller.enablePan = true
  engine.controller.enableTilt = true
  buildingTileset?.releaseCameraViewport()
  roadTileset?.releaseCameraViewport()
}

function markInteracting(): void {
  if (!interacting.value) {
    buildingTileset && (buildingTileset.errorTarget = BUILDING_MOVING_ERROR_TARGET)
    vegetationRenderer?.setInteractionActive(true)
  }
  interacting.value = true
  enableCameraInteraction()
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  interactionEndTimer = setTimeout(() => {
    interacting.value = false
    if (buildingTileset) buildingTileset.errorTarget = BUILDING_IDLE_ERROR_TARGET
    vegetationRenderer?.setInteractionActive(false)
    const stats = vehicleRenderer?.refreshViewport()
    if (stats) updateVehicleRenderStats(stats)
    engine?.requestRender()
    interactionEndTimer = null
  }, 300)
}

function updateVehicleRenderStats(stats: VehicleRenderStats): void {
  vehicleStats.value = stats
  const current = snapshot.value
  if (
    showRenderDiagnostics
    && current?.state === 'RUNNING'
    && (current.metrics.active_vehicles ?? 0) > 0
    && stats.visibleCount === 0
    && current.sequence - lastEmptyVehicleWarningSequence >= 25
  ) {
    lastEmptyVehicleWarningSequence = current.sequence
    console.warn('[vehicle-render] active simulation has no vehicles inside the camera radius', {
      activeVehicles: current.metrics.active_vehicles,
      inputVehicles: stats.inputCount,
      renderRadiusMeters: Math.round(stats.radiusMeters),
      snapshotSequence: current.sequence,
    })
  }
}

function syncAnimationLoop(): void {
  if (!engine) return
  const state = snapshot.value?.state
  const active = state === 'STARTING' || state === 'RUNNING' || state === 'STOPPING'
  if (engine.rendering.enableAnimationLoop !== active) {
    engine.rendering.enableAnimationLoop = active
    engine.requestRender()
  }
}

function updateTilesStatus(): void {
  const activeTilesets = [buildingTileset, roadTileset].filter(
    (value): value is mapvthree.Default3DTiles => value !== null,
  )
  if (activeTilesets.length === 0) return
  const ready = activeTilesets.reduce(
    (sum, value) => sum + value.statistics.numberOfTilesWithContentReady,
    0,
  )
  const pending = activeTilesets.reduce(
    (sum, value) => sum
      + value.statistics.numberOfPendingRequests
      + value.statistics.numberOfTilesProcessing,
    0,
  )
  const loaded = activeTilesets.reduce(
    (sum, value) => sum + value.statistics.numberOfLoadedTilesTotal,
    0,
  )
  const total = activeTilesets.reduce(
    (sum, value) => sum + value.statistics.numberOfTilesTotal,
    0,
  )
  const nextRoadTilesReady = Boolean(
    roadTileset
    && (roadTileset.statistics.numberOfTilesWithContentReady > 0
      || roadTileset.statistics.numberOfLoadedTilesTotal > 0),
  )
  if (nextRoadTilesReady !== roadTilesReady) {
    roadTilesReady = nextRoadTilesReady
    syncRoadRendering(geojson.value)
  }

  if (ready > 0 || loaded > 0) {
    tilesStatus.value = 'ready'
    tilesMessage.value = `3D Tiles 已就绪 · 可见 ${ready} · 已载 ${loaded}${total > 0 ? `/${total}` : ''}`
    return
  }
  tilesStatus.value = 'loading'
  tilesMessage.value = `3D Tiles 加载中 · 请求 ${pending}`
}

function syncRoadRendering(response: MapGeoJsonResponse | null): void {
  const staticRoadMatches = response
    ? Boolean(
      roadTilesetManifest
      && roadTilesetMatchesResponse(roadTilesetManifest, response, scenePlacement),
    )
    : Boolean(
      roadTilesetManifest
      && roadTilesetManifestIsValid(roadTilesetManifest, scenePlacement),
    )
  if (roadTileset) roadTileset.visible = staticRoadMatches && !realisticDetailReady
  if (response && roadTileset && staticRoadMatches && roadTilesReady && !realisticDetailReady) {
    roadRenderer?.clear()
    return
  }
  roadRenderer?.render(response)
  if (roadRenderer instanceof BaiduDetailedRoadRenderer) {
    roadRenderer.setRealisticDetailActive(realisticDetailReady)
  }
}

async function switchRealisticIntersection(intersectionId: string): Promise<void> {
  if (!realisticIntersectionLayer || !intersectionId) return
  setSceneLoading()
  try {
    const manifest = await realisticIntersectionLayer.switchTo(intersectionId)
    realisticDetailReady = true
    syncRoadRendering(geojson.value)
    roadsideFacilityRenderer?.setRealisticDetailActive(true)
    realisticIntersectionLayer.updateSignals(trafficView.value?.intersections ?? null)
    setSceneReady()
    mapView.setCameraPreset('intersection')
    mapView.flyTo(
      [manifest.origin.longitude, manifest.origin.latitude],
      19,
      `intersection:${manifest.intersectionId}`,
    )
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') return
    realisticDetailReady = realisticIntersectionLayer.activeIntersectionId !== null
    syncRoadRendering(geojson.value)
    roadsideFacilityRenderer?.setRealisticDetailActive(realisticDetailReady)
    setSceneError(cause instanceof Error ? cause.message : '高精度路口加载失败')
  }
}

function syncMapRendering(response: MapGeoJsonResponse | null): void {
  syncRoadRendering(response)
  showcaseModelLayers?.render(enableJunctionMarkers ? response : null)
  if (engine && response?.bounds) {
    const latitudePadding = 300 / 110_900
    const longitudePadding = latitudePadding / Math.cos(response.center.latitude * Math.PI / 180)
    const southwest = coordinateProjector([
      response.bounds.west - longitudePadding,
      response.bounds.south - latitudePadding,
    ])
    const northeast = coordinateProjector([
      response.bounds.east + longitudePadding,
      response.bounds.north + latitudePadding,
    ])
    engine.map.setBounds([
      [southwest[0], southwest[1]],
      [northeast[0], northeast[1]],
    ])
  }
  if (!response || !showcaseModelLayers || !showcaseLandmarkUrl || showcaseLandmarkPromise) return
  showcaseLandmarkPromise = showcaseModelLayers.loadLandmark({
    url: showcaseLandmarkUrl,
    position: [response.center.longitude, response.center.latitude, 0],
  }).catch((cause: unknown) => {
    console.warn('[showcase-layers] landmark layer disabled', cause)
  })
}

async function loadRoadsideFacilities(): Promise<void> {
  const response = await fetch(roadsideFacilitiesUrl)
  if (!response.ok) throw new Error(`Roadside facilities returned HTTP ${response.status}`)
  const manifest = parseSceneFacilityManifest(await response.json())
  if (!roadsideFacilityRenderer) return
  roadsideFacilityRenderer.render(manifest)
  roadsideFacilityRenderer.updateSignals(trafficView.value?.intersections ?? null)
}

function scheduleVegetationLoad(): void {
  if (!engine || !enableVegetation) return
  vegetationRenderer = new VegetationRenderer(engine, coordinateProjector)
  vegetationRenderer.setInteractionActive(interacting.value)
  vegetationLoadTimer = setTimeout(() => {
    vegetationLoadTimer = null
    void vegetationRenderer?.load(vegetationManifestUrl, vegetationModelUrl).catch((cause: unknown) => {
      console.warn('[vegetation] layer disabled', cause)
    })
  }, 450)
}

function scheduleBuildingTilesetLoad(): void {
  if (!engine || !enableLocalTileset || buildingTileset || buildingLoadTimer) return
  tilesStatus.value = 'loading'
  buildingLoadTimer = setTimeout(() => {
    buildingLoadTimer = null
    if (!engine) return
    buildingTileset = engine.add(new mapvthree.Default3DTiles({
      url: tilesetUrl,
      errorTarget: interacting.value
        ? BUILDING_MOVING_ERROR_TARGET
        : BUILDING_IDLE_ERROR_TARGET,
      forceUnlit: false,
      dynamicScreenSpaceError: true,
      foveatedScreenSpaceError: true,
      foveatedConeSize: 0.22,
      foveatedMinimumScreenSpaceErrorRelaxation: 1.2,
      progressiveResolutionHeightFraction: 0.35,
      cullRequestsWhileMoving: true,
      cullRequestsWhileMovingMultiplier: 120,
      cacheBytes: 192 * 1024 * 1024,
    })) as mapvthree.Default3DTiles
    buildingTileset.releaseCameraViewport()
    engine.requestRender()
  }, 650)
}

async function addStaticRoadTileset(): Promise<void> {
  if (!engine || !enableStaticRoadTileset) return
  const manifestUrl = new URL(
    './manifest.json',
    new URL(roadTilesetUrl, window.location.href),
  )
  const response = await fetch(manifestUrl)
  if (!response.ok) throw new Error(`道路 3D Tiles manifest 加载失败 (${response.status})`)
  roadTilesetManifest = await response.json() as StaticRoadTilesetManifest
  roadTileset = engine.add(new mapvthree.Default3DTiles({
    url: roadTilesetUrl,
    errorTarget: 8,
    forceUnlit: true,
    dynamicScreenSpaceError: false,
    foveatedScreenSpaceError: false,
    cullRequestsWhileMoving: true,
    cullRequestsWhileMovingMultiplier: 120,
    cacheBytes: 32 * 1024 * 1024,
  })) as mapvthree.Default3DTiles
  roadTileset.releaseCameraViewport()
}

function bindContainerInteraction(container: HTMLElement): void {
  container.addEventListener('pointerdown', markInteracting, { passive: true })
  container.addEventListener('pointermove', markInteracting, { passive: true })
  container.addEventListener('wheel', markInteracting, { passive: true })
}

function unbindContainerInteraction(container: HTMLElement | null): void {
  if (!container) return
  container.removeEventListener('pointerdown', markInteracting)
  container.removeEventListener('pointermove', markInteracting)
  container.removeEventListener('wheel', markInteracting)
}

async function initMap(): Promise<void> {
  const container = containerRef.value
  if (!container) return
  if (!baiduAk) {
    throw new Error('未配置 VITE_BAIDU_MAP_AK，请先填写百度地图浏览器端 AK')
  }

  mapvthree.BaiduMapConfig.ak = baiduAk
  engine = new mapvthree.Engine(container, {
    map: {
      projection: mapvthree.PROJECTION_WEB_MERCATOR,
      center: sceneCenter,
      pitch: 58,
      heading: 30,
      range: DEFAULT_CESIUM_CAMERA_HEIGHT,
      is3DControl: true,
      provider: createBaiduProvider(),
    },
    rendering: {
      sky: null,
      enableAnimationLoop: false,
      animationLoopFrameTime: ACTIVE_FRAME_TIME_MS,
    },
  })
  engine.map.setMinRange(BAIDU_3D_MIN_RANGE)
  engine.map.setMaxRange(BAIDU_3D_MAX_RANGE)
  sky = engine.add(new mapvthree.DefaultSky())
  sky.color = new Color('#152535')
  sky.highColor = new Color('#07111d')
  sky.skyLightIntensity = 0.78
  enableCameraInteraction()
  bindContainerInteraction(container)

  if (enableLocalTileset) {
    tilesMessage.value = '3D Tiles 已加入场景，正在加载可见建筑…'
  } else {
    tilesStatus.value = 'ready'
    tilesMessage.value = showBaiduBuildings
      ? '已启用百度地图 3D 建筑 · 本地 3D Tiles 暂停加载'
      : '本地 3D Tiles 暂停加载 · 百度建筑已关闭'
  }

  await addStaticRoadTileset().catch((cause: unknown) => {
    roadTilesetManifest = null
    roadTileset = null
    console.warn(cause instanceof Error ? cause.message : cause)
  })
  if (enableLocalTileset || roadTileset) {
    tilesStatusTimer = setInterval(updateTilesStatus, 500)
  }

  roadRenderer = roadRendererMode === 'basic'
    ? new BaiduRoadNetworkRenderer(engine, coordinateProjector)
    : new BaiduDetailedRoadRenderer(engine, coordinateProjector)
  vehicleRenderer = new BaiduVehicleRenderer(engine, coordinateProjector)
  realisticIntersectionLayer = new MapvRealisticIntersectionLayer(engine, coordinateProjector)
  if (enableShowcaseLayers) {
    showcaseGeoJsonLayers = new ShowcaseGeoJsonLayers(engine, coordinateProjector)
    showcaseModelLayers = new ShowcaseModelLayers(engine, coordinateProjector)
    void showcaseGeoJsonLayers.load({
      water: import.meta.env.VITE_SHOWCASE_WATER_GEOJSON_URL?.trim(),
      green: import.meta.env.VITE_SHOWCASE_GREEN_GEOJSON_URL?.trim(),
      urban: import.meta.env.VITE_SHOWCASE_URBAN_GEOJSON_URL?.trim(),
      buildings: import.meta.env.VITE_SHOWCASE_BUILDINGS_GEOJSON_URL?.trim(),
      labels: import.meta.env.VITE_SHOWCASE_LABEL_GEOJSON_URL?.trim(),
    })
  }
  if (enableRoadsideFacilities) {
    roadsideFacilityRenderer = new RoadsideFacilityRenderer(engine, coordinateProjector)
    void loadRoadsideFacilities().catch((cause: unknown) => {
      console.warn('[roadside-facilities] layer disabled', cause)
    })
  }
  scheduleBuildingTilesetLoad()
  scheduleVegetationLoad()
  watch(
    trafficView,
    (value) => {
      const current = snapshot.value
      const stats = vehicleRenderer?.update(value?.vehicles ?? [], {
        sessionId: current?.session_id ?? '',
        state: current?.state ?? null,
        sequence: current?.sequence ?? -1,
        elapsedSeconds: current?.elapsed_seconds ?? 0,
      })
      if (stats) updateVehicleRenderStats(stats)
      roadsideFacilityRenderer?.updateSignals(value?.intersections ?? null)
      realisticIntersectionLayer?.updateSignals(value?.intersections ?? null)
    },
    { immediate: true },
  )
  watch(
    activeIntersectionId,
    (intersectionId) => { void switchRealisticIntersection(intersectionId) },
    { immediate: true },
  )
  watch(snapshot, syncAnimationLoop, { immediate: true })
  watch(
    geojson,
    syncMapRendering,
    { immediate: true },
  )

  mapView.registerThreeMap({
    flyTo: (target, options) => {
      if (!interacting.value) {
        engine?.map.flyTo(placeBaiduCameraTarget(target, scenePlacement), options)
      }
    },
    setViewport: (points, options) => {
      if (!interacting.value) {
        engine?.map.setViewport(
          points.map((point) => placeBaiduCameraTarget(point, scenePlacement)),
          options,
        )
      }
    },
  })
  engine.requestRender()
  loading.value = false
}

onMounted(() => {
  void initMap().catch((cause: unknown) => {
    error.value = cause instanceof Error ? cause.message : '百度三维地图初始化失败'
    tilesStatus.value = 'error'
    tilesMessage.value = error.value
    loading.value = false
  })
})

onUnmounted(() => {
  mapView.unregisterThreeMap()
  unbindContainerInteraction(containerRef.value)
  if (tilesStatusTimer) clearInterval(tilesStatusTimer)
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  if (vegetationLoadTimer) clearTimeout(vegetationLoadTimer)
  if (buildingLoadTimer) clearTimeout(buildingLoadTimer)
  tilesStatusTimer = null
  interactionEndTimer = null
  vegetationLoadTimer = null
  buildingLoadTimer = null
  roadRenderer?.destroy()
  roadRenderer = null
  vehicleRenderer?.destroy()
  vehicleRenderer = null
  showcaseGeoJsonLayers?.destroy()
  showcaseGeoJsonLayers = null
  showcaseModelLayers?.destroy()
  showcaseModelLayers = null
  roadsideFacilityRenderer?.destroy()
  roadsideFacilityRenderer = null
  vegetationRenderer?.destroy()
  vegetationRenderer = null
  realisticIntersectionLayer?.destroy()
  realisticIntersectionLayer = null
  realisticDetailReady = false
  showcaseLandmarkPromise = null
  if (sky && engine) engine.remove(sky)
  sky = null
  if (buildingTileset && engine) engine.remove(buildingTileset)
  if (roadTileset && engine) engine.remove(roadTileset)
  buildingTileset = null
  roadTileset = null
  roadTilesetManifest = null
  roadTilesReady = false
  engine?.dispose()
  engine = null
})
</script>

<template>
  <div class="app-baidu-three-map">
    <div ref="containerRef" class="app-baidu-three-map__canvas" />
    <div v-if="loading" class="app-baidu-three-map__overlay">正在加载百度三维地图与 3D 建筑…</div>
    <div
      v-else-if="error"
      class="app-baidu-three-map__overlay app-baidu-three-map__overlay--error"
    >
      {{ error }}
    </div>
    <div class="app-baidu-three-map__status" :class="`is-${tilesStatus}`">
      <span class="app-baidu-three-map__status-dot" />
      <span>{{ tilesMessage }}</span>
      <span v-if="interacting"> · 自由视角</span>
    </div>
    <div class="app-baidu-three-map__detail-status" :class="`is-${sceneStatus}`">
      <span class="app-baidu-three-map__status-dot" />
      <span v-if="sceneStatus === 'loading'">正在加载 {{ activeIntersectionId }} 高精度路口</span>
      <span v-else-if="sceneStatus === 'ready'">{{ activeIntersectionId }} 高精度路口已启用</span>
      <span v-else-if="sceneStatus === 'error'">{{ sceneError }}</span>
      <span v-else>高精度路口待加载</span>
    </div>
    <div
      v-if="showRenderDiagnostics"
      class="app-baidu-three-map__vehicle-diagnostics"
      aria-label="车辆渲染诊断"
    >
      车辆 {{ vehicleStats.visibleCount }}/{{ vehicleStats.inputCount }} · 半径
      {{ Math.round(vehicleStats.radiusMeters) }}m · 快照 {{ snapshot?.sequence ?? 0 }}
    </div>
  </div>
</template>

<style scoped>
.app-baidu-three-map {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  background: #0d1b2a;
}

.app-baidu-three-map__canvas {
  width: 100%;
  height: 100%;
  touch-action: none;
  pointer-events: auto;
}

.app-baidu-three-map__overlay {
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

.app-baidu-three-map__overlay--error {
  color: #ffb4b4;
}

.app-baidu-three-map__status {
  position: absolute;
  right: calc(var(--dashboard-panel-inset-right, 30px) + 610px);
  bottom: 28px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid rgba(33, 230, 255, 0.24);
  border-radius: 999px;
  background: rgba(2, 10, 24, 0.78);
  color: #9edfff;
  font-size: 11px;
  pointer-events: none;
}

.app-baidu-three-map__detail-status {
  position: absolute;
  left: 50%;
  bottom: 28px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 10px;
  border: 1px solid rgba(33, 230, 255, 0.26);
  border-radius: 4px;
  background: rgba(2, 10, 24, 0.82);
  color: #9edfff;
  font-size: 11px;
  transform: translateX(-50%);
  pointer-events: none;
}

.app-baidu-three-map__detail-status.is-ready .app-baidu-three-map__status-dot {
  background: #3ce69a;
}

.app-baidu-three-map__detail-status.is-error {
  color: #ffb4b4;
}

.app-baidu-three-map__vehicle-diagnostics {
  position: absolute;
  left: 50%;
  bottom: 57px;
  z-index: 2;
  padding: 4px 8px;
  border-left: 2px solid rgba(88, 240, 174, 0.72);
  background: rgba(2, 10, 24, 0.76);
  color: #91b8ce;
  font-size: 10px;
  transform: translateX(-50%);
  pointer-events: none;
}

.app-baidu-three-map__status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e8b94c;
  box-shadow: 0 0 7px currentColor;
}

.app-baidu-three-map__status.is-ready .app-baidu-three-map__status-dot {
  background: #3ce69a;
}

.app-baidu-three-map__status.is-error {
  color: #ffb4b4;
}

.app-baidu-three-map__status.is-error .app-baidu-three-map__status-dot {
  background: #ff6b6b;
}

@media (max-width: 1320px) {
  .app-baidu-three-map__status {
    right: 18px;
    bottom: 18px;
  }
}
</style>
