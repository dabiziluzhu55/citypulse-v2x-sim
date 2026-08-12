<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppBackgroundMap from './components/visualization/AppBackgroundMap.vue'
import AppMapGradientMask from './components/visualization/AppMapGradientMask.vue'
import AppThreeMapLoader from './components/visualization/AppThreeMapLoader.vue'
import DashboardChrome from './components/dashboard/chrome/DashboardChrome.vue'
import { provideAppMapView } from './composables/useAppMapView'

const route = useRoute()
const mapView = provideAppMapView()
const mapDimension = computed(() => mapView.dimension.value)
const isStandaloneRoute = computed(() => route.meta.standalone === true)
const threeMapState = ref<'loading' | 'ready' | 'error'>('loading')
const threeMapMounted = ref(false)
const map2dMounted = ref(true)
const displayedMapDimension = ref<'2d' | '3d'>(mapDimension.value)
const map2dActive = ref(mapDimension.value === '2d')
const map3dActive = ref(mapDimension.value === '3d')
let mapTransitionRevision = 0
const threeMapBlocked = computed(() => (
  mapDimension.value === '3d' && threeMapState.value !== 'ready'
))

function waitForRenderFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()))
}

watch(mapDimension, async (dimension) => {
  const revision = ++mapTransitionRevision
  if (dimension === '3d') {
    threeMapMounted.value = true
    map3dActive.value = true
  } else {
    map2dMounted.value = true
    map2dActive.value = true
  }
  await nextTick()
  await waitForRenderFrame()
  await waitForRenderFrame()
  if (revision !== mapTransitionRevision) return
  displayedMapDimension.value = dimension
  window.setTimeout(() => {
    if (revision !== mapTransitionRevision) return
    map2dActive.value = dimension === '2d'
    map3dActive.value = dimension === '3d'
  }, 120)
}, { immediate: true })

function handleThreeMapState(nextState: 'loading' | 'ready' | 'error'): void {
  threeMapState.value = nextState
  if (nextState === 'ready' && mapDimension.value === '3d') {
    const revision = mapTransitionRevision
    window.setTimeout(() => {
      if (
        revision === mapTransitionRevision
        && mapDimension.value === '3d'
        && threeMapState.value === 'ready'
      ) map2dMounted.value = false
    }, 140)
  }
}

function returnTo2d(): void {
  threeMapState.value = 'loading'
  mapView.setDimension('2d')
}
</script>

<template>
  <router-view v-if="isStandaloneRoute" />
  <div v-else class="app-shell app-shell--dashboard">
    <div
      class="app-map-layer"
      :class="{ 'is-visible': displayedMapDimension === '2d' }"
    >
      <AppBackgroundMap v-if="map2dMounted" :active="map2dActive" />
    </div>
    <div
      v-if="threeMapMounted"
      class="app-map-layer"
      :class="{ 'is-visible': displayedMapDimension === '3d' }"
    >
      <AppThreeMapLoader
        :active="map3dActive"
        @return2d="returnTo2d"
        @state-change="handleThreeMapState"
      />
    </div>
    <AppMapGradientMask />

    <DashboardChrome />

    <div
      class="app-content app-content--dashboard"
      :class="{ 'app-content--map3d-blocked': threeMapBlocked }"
    >
      <main class="app-main app-main--dashboard">
        <router-view />
      </main>
    </div>

    <div class="app-map-attribution">
      <template v-if="mapDimension === '3d'">
        Data attribution ©
        <a href="https://lbsyun.baidu.com/" target="_blank" rel="noopener noreferrer">百度地图</a>
        ，雄安新区 3D Tiles
      </template>
      <template v-else>
        ©
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">
          OpenStreetMap
        </a>
        contributors
      </template>
    </div>

  </div>
</template>

<style scoped>
.app-shell--dashboard {
  height: 100vh;
  overflow: hidden;
}

.app-map-layer {
  position: absolute;
  inset: 0;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 120ms linear;
}

.app-map-layer.is-visible {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
}

.app-shell {
  position: relative;
  min-height: 100vh;
  color: var(--cp-text-primary);
}

.app-content {
  position: relative;
  z-index: 2;
  min-height: 100vh;
  padding: 24px;
  pointer-events: none;
}

.app-content--dashboard {
  height: 100vh;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}

.app-content :deep(a),
.app-content :deep(button),
.app-content :deep(input),
.app-content :deep(textarea),
.app-content :deep(select),
.app-content :deep(.el-input),
.app-content :deep(.el-select),
.app-content :deep(.el-radio),
.app-content :deep(.el-checkbox),
.app-content :deep(.el-button),
.app-content :deep(.el-steps),
.app-content :deep(.el-alert),
.app-content :deep(.el-skeleton),
.app-content :deep(.section-panel),
.app-content :deep(.result-panel),
.app-content :deep(.preview-panel),
.app-content :deep(.dashboard-panel),
.app-content :deep(.map-overlay),
.app-content :deep(.map-legend),
.app-content :deep(.timeline),
.app-content :deep(.map-hint),
.app-content :deep(.map-loading),
.app-content :deep(.map-alert),
.app-content :deep(.dashboard-panel-title),
.app-content :deep(.map-dimension-toggle),
.app-content :deep(.dashboard-bottom-icons__btn) {
  pointer-events: auto;
}

.app-content--map3d-blocked :deep(*) {
  pointer-events: none !important;
}

.app-content--map3d-blocked :deep(.map-dimension-toggle),
.app-content--map3d-blocked :deep(.map-dimension-toggle *) {
  pointer-events: auto !important;
}

.app-main {
  width: min(1920px, 100%);
  margin: 0 auto;
}

.app-main--dashboard {
  width: 100%;
  max-width: none;
  height: 100%;
  overflow: hidden;
}

.app-map-attribution {
  position: fixed;
  left: 190px;
  bottom: 36px;
  z-index: 3;
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(1, 14, 26, 0.78);
  color: #78aeca;
  font-size: 11px;
  pointer-events: auto;
}

.app-shell--dashboard .app-map-attribution {
  left: calc(var(--dashboard-panel-inset, 30px) + 8px);
  bottom: 24px;
}

.app-map-attribution a {
  color: #21e6ff;
}

@media (max-width: 900px) {
  .app-content--dashboard {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }

}
</style>
