<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const AMAP_KEY = import.meta.env.VITE_AMAP_MAP_KEY?.trim() || 'caffa74076c7fa91c91b133e0fa9fb20'
const WGS84_ROOT_CENTER: [number, number] = [115.95498986829843, 38.986485772313685]
const GCJ02_ROOT_CENTER: [number, number] = [115.96086939777948, 38.987238499128665]
const BD09_ROOT_CENTER: [number, number] = [115.96742068199087, 38.99304707932014]

const containerRef = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const status = ref('正在加载高德 3D 地图…')

let map: AMap.Map | null = null
let scriptEl: HTMLScriptElement | null = null

function loadAmapScript(): Promise<void> {
  if (window.AMap) return Promise.resolve()

  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-amap-jsapi="true"]')
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true })
      existing.addEventListener('error', () => reject(new Error('高德 JSAPI 加载失败')), { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${AMAP_KEY}`
    script.async = true
    script.dataset.amapJsapi = 'true'
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('高德 JSAPI 加载失败，请检查 key、网络或安全域名配置'))
    document.head.appendChild(script)
    scriptEl = script
  })
}

function addMarker(
  position: [number, number],
  label: string,
  color: string,
): void {
  if (!map || !window.AMap) return

  const marker = new window.AMap.Marker({
    position,
    anchor: 'bottom-center',
    content: `<div class="amap-test-marker" style="--marker-color:${color}"></div>`,
    offset: new window.AMap.Pixel(0, 0),
  })
  marker.setLabel({
    direction: 'top',
    offset: new window.AMap.Pixel(0, -8),
    content: `<div class="amap-test-label">${label}</div>`,
  })
  map.add(marker)
}

async function initMap(): Promise<void> {
  await loadAmapScript()
  if (!containerRef.value || !window.AMap) return

  map = new window.AMap.Map(containerRef.value, {
    viewMode: '3D',
    zoom: 16.3,
    pitch: 65,
    rotation: -18,
    center: GCJ02_ROOT_CENTER,
    mapStyle: 'amap://styles/darkblue',
    showBuildingBlock: true,
    buildingAnimation: true,
    skyColor: '#071426',
  })

  addMarker(GCJ02_ROOT_CENTER, 'GCJ-02 / 高德根中心', '#34f5c5')
  addMarker(WGS84_ROOT_CENTER, 'WGS84 原始根中心', '#ffcf5a')
  addMarker(BD09_ROOT_CENTER, 'BD-09 / 百度根中心', '#ff6f91')

  map.on('complete', () => {
    loading.value = false
    status.value = '高德 3D 地图已加载。绿色点是推荐用于高德底图的 3D Tiles 根中心。'
  })
}

onMounted(() => {
  void initMap().catch((cause: unknown) => {
    error.value = cause instanceof Error ? cause.message : '高德 3D 地图初始化失败'
    status.value = error.value
    loading.value = false
  })
})

onUnmounted(() => {
  map?.destroy()
  map = null
  if (scriptEl?.parentNode && !window.AMap) {
    scriptEl.parentNode.removeChild(scriptEl)
  }
  scriptEl = null
})
</script>

<template>
  <main class="amap-test-page">
    <div ref="containerRef" class="amap-test-page__map" />
    <section class="amap-test-page__panel">
      <p class="amap-test-page__eyebrow">Gaode / AMap 3D diagnostic</p>
      <h1>高德 3D 地图最小验证</h1>
      <p>{{ status }}</p>
      <ul>
        <li><span class="legend legend--gcj" />GCJ-02：高德底图推荐对齐点</li>
        <li><span class="legend legend--wgs" />WGS84：当前 tileset ECEF 反算点</li>
        <li><span class="legend legend--bd" />BD-09：百度底图坐标点</li>
      </ul>
      <p class="amap-test-page__note">
        如果商家数据基于高德白模，绿色点附近的高德道路/建筑关系应最接近当前 3D Tiles 的真实基准。
      </p>
    </section>
    <div v-if="loading" class="amap-test-page__loading">正在加载高德 3D 地图…</div>
    <div v-if="error" class="amap-test-page__error">{{ error }}</div>
  </main>
</template>

<style scoped>
.amap-test-page {
  position: fixed;
  inset: 0;
  overflow: hidden;
  background: #06111f;
  color: #e8f7ff;
}

.amap-test-page__map {
  position: absolute;
  inset: 0;
}

.amap-test-page__panel {
  position: absolute;
  left: 24px;
  top: 24px;
  z-index: 2;
  width: min(420px, calc(100vw - 48px));
  padding: 20px 22px;
  border: 1px solid rgba(52, 245, 197, 0.28);
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(5, 18, 34, 0.9), rgba(6, 33, 51, 0.78));
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(16px);
}

.amap-test-page__eyebrow {
  margin: 0 0 8px;
  color: #34f5c5;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.amap-test-page__panel h1 {
  margin: 0 0 10px;
  font-size: 22px;
}

.amap-test-page__panel p {
  margin: 0 0 14px;
  color: #b8d9e9;
  font-size: 14px;
  line-height: 1.7;
}

.amap-test-page__panel ul {
  display: grid;
  gap: 8px;
  margin: 0 0 14px;
  padding: 0;
  list-style: none;
  color: #d6efff;
  font-size: 13px;
}

.legend {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 8px;
  border-radius: 50%;
  box-shadow: 0 0 12px currentColor;
}

.legend--gcj {
  background: #34f5c5;
  color: #34f5c5;
}

.legend--wgs {
  background: #ffcf5a;
  color: #ffcf5a;
}

.legend--bd {
  background: #ff6f91;
  color: #ff6f91;
}

.amap-test-page__note {
  color: #8fbacf !important;
  font-size: 12px !important;
}

.amap-test-page__loading,
.amap-test-page__error {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 3;
  transform: translate(-50%, -50%);
  padding: 12px 18px;
  border-radius: 999px;
  background: rgba(4, 15, 28, 0.86);
  color: #9fefff;
}

.amap-test-page__error {
  color: #ffb9b9;
}

:global(.amap-test-marker) {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.92);
  border-radius: 50%;
  background: var(--marker-color);
  box-shadow: 0 0 0 6px color-mix(in srgb, var(--marker-color) 24%, transparent), 0 0 18px var(--marker-color);
}

:global(.amap-test-label) {
  padding: 4px 8px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 999px;
  background: rgba(2, 10, 22, 0.86);
  color: #effbff;
  font-size: 12px;
  white-space: nowrap;
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.32);
}
</style>
