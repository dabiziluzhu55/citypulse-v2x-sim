<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as mapvthree from '@baidumap/mapv-three'
import { bindThreeMapInstance, useAppMapView } from '../../composables/useAppMapView'
import { useSimulationMap } from '../../composables/useSimulationMap'
import { useSimulationStore } from '../../composables/useSimulationStore'
import { BaiduDetailedRoadRenderer } from '../../mapv/BaiduDetailedRoadRenderer'
import { BaiduRoadNetworkRenderer } from '../../mapv/BaiduRoadNetworkRenderer'
import { BaiduVehicleRenderer } from '../../mapv/BaiduVehicleRenderer'
import { DEFAULT_CESIUM_CAMERA_HEIGHT } from '../../constants/mapDefaults'
import { DEMO_2_SOURCE_CENTER_BD09 } from '../../mapv/sceneCoordinates'


const containerRef = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const { geojson } = useSimulationMap()
const { trafficView } = useSimulationStore()

const loading = ref(true)
const error = ref<string | null>(null)
const tilesStatus = ref<'loading' | 'ready' | 'error'>('loading')
const tilesMessage = ref('正在加载百度地图 3D 建筑…')
const interacting = ref(false)

let engine: mapvthree.Engine | null = null
let tileset: mapvthree.Default3DTiles | null = null
let roadRenderer: BaiduRoadNetworkRenderer | BaiduDetailedRoadRenderer | null = null
let vehicleRenderer: BaiduVehicleRenderer | null = null
let tilesStatusTimer: ReturnType<typeof setInterval> | null = null
let interactionEndTimer: ReturnType<typeof setTimeout> | null = null

const tilesetUrl =
  import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim() || '/3dtiles/xiongan/tileset.json'
const enableLocalTileset = import.meta.env.VITE_ENABLE_XIONGAN_3DTILES === 'true'
const baiduAk = import.meta.env.VITE_BAIDU_MAP_AK?.trim() || ''
const showBaiduBuildings = import.meta.env.VITE_BAIDU_BUILDINGS !== 'false'
const roadRendererMode = import.meta.env.VITE_BAIDU_ROAD_RENDERER?.trim() || 'detailed'

function createBaiduProvider(): mapvthree.BaiduVectorTileProvider {
  return new mapvthree.BaiduVectorTileProvider({
    ak: baiduAk,
    displayOptions: {
      base: true,
      link: true,
      building: showBaiduBuildings,
      poi: false,
      flat: !showBaiduBuildings,
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
  tileset?.releaseCameraViewport()
}

function markInteracting(): void {
  interacting.value = true
  enableCameraInteraction()
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  interactionEndTimer = setTimeout(() => {
    interacting.value = false
    interactionEndTimer = null
  }, 220)
}

function updateTilesStatus(): void {
  if (!tileset) return
  const statistics = tileset.statistics
  const ready = statistics.numberOfTilesWithContentReady
  const pending = statistics.numberOfPendingRequests + statistics.numberOfTilesProcessing
  const loaded = statistics.numberOfLoadedTilesTotal
  const total = statistics.numberOfTilesTotal

  if (ready > 0 || loaded > 0) {
    tilesStatus.value = 'ready'
    tilesMessage.value = `3D Tiles 已就绪 · 可见 ${ready} · 已载 ${loaded}${total > 0 ? `/${total}` : ''}`
    return
  }
  tilesStatus.value = 'loading'
  tilesMessage.value = `3D Tiles 加载中 · 请求 ${pending}`
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
      center: DEMO_2_SOURCE_CENTER_BD09,
      pitch: 55,
      range: DEFAULT_CESIUM_CAMERA_HEIGHT,
      provider: createBaiduProvider(),
    },
    rendering: {
      sky: null,
      enableAnimationLoop: true,
    },
  })
  enableCameraInteraction()
  bindContainerInteraction(container)

  if (enableLocalTileset) {
    tileset = engine.add(new mapvthree.Default3DTiles({
      url: tilesetUrl,
      errorTarget: 24,
      forceUnlit: true,
      dynamicScreenSpaceError: true,
      foveatedScreenSpaceError: true,
      cacheBytes: 384 * 1024 * 1024,
    })) as mapvthree.Default3DTiles
    tileset.releaseCameraViewport()
    tilesMessage.value = '3D Tiles 已加入场景，正在加载可见建筑…'
    tilesStatusTimer = setInterval(updateTilesStatus, 500)
  } else {
    tilesStatus.value = 'ready'
    tilesMessage.value = showBaiduBuildings
      ? '已启用百度地图 3D 建筑 · 本地 3D Tiles 暂停加载'
      : '本地 3D Tiles 暂停加载 · 百度建筑已关闭'
  }

  roadRenderer = roadRendererMode === 'basic'
    ? new BaiduRoadNetworkRenderer(engine)
    : new BaiduDetailedRoadRenderer(engine)
  vehicleRenderer = new BaiduVehicleRenderer(engine)
  watch(
    trafficView,
    (value) => vehicleRenderer?.update(value?.vehicles ?? []),
    { immediate: true },
  )
  watch(
    geojson,
    (value) => roadRenderer?.render(value),
    { immediate: true },
  )

  bindThreeMapInstance(mapView, {
    flyTo: (target, options) => {
      if (!interacting.value) engine?.map.flyTo(target, options)
    },
    setViewport: (points, options) => {
      if (!interacting.value) engine?.map.setViewport(points, options)
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
  unbindContainerInteraction(containerRef.value)
  if (tilesStatusTimer) clearInterval(tilesStatusTimer)
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  tilesStatusTimer = null
  interactionEndTimer = null
  roadRenderer?.destroy()
  roadRenderer = null
  vehicleRenderer?.destroy()
  vehicleRenderer = null
  if (tileset && engine) engine.remove(tileset)
  tileset = null
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
