<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppBackgroundMap from './components/visualization/AppBackgroundMap.vue'
import AppMapGradientMask from './components/visualization/AppMapGradientMask.vue'
import AppThreeMapLoader from './components/visualization/AppThreeMapLoader.vue'
import DashboardChrome from './components/dashboard/chrome/DashboardChrome.vue'
import { provideAppMapView } from './composables/useAppMapView'
import type { Map3dFailure } from './mapv/map3dLoadRecovery'

const route = useRoute()
const mapView = provideAppMapView()
const mapDimension = computed(() => mapView.dimension.value)
const isStandaloneRoute = computed(() => route.meta.standalone === true)
const threeMapFailure = ref<Map3dFailure | null>(null)

watch(mapDimension, (next) => {
  if (next === '3d') threeMapFailure.value = null
})

function handleThreeMapFailure(failure: Map3dFailure): void {
  threeMapFailure.value = failure
  mapView.setDimension('2d')
}

function retryThreeMap(): void {
  threeMapFailure.value = null
  mapView.setDimension('3d')
}
</script>

<template>
  <router-view v-if="isStandaloneRoute" />
  <div v-else class="app-shell app-shell--dashboard">
    <AppBackgroundMap v-if="mapDimension === '2d'" />
    <AppThreeMapLoader v-else @fatal="handleThreeMapFailure" />
    <AppMapGradientMask />

    <DashboardChrome />

    <div class="app-content app-content--dashboard">
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

    <div
      v-if="threeMapFailure"
      class="app-map-recovery"
      role="alert"
      aria-live="assertive"
    >
      <span class="app-map-recovery__status" aria-hidden="true" />
      <div class="app-map-recovery__content">
        <strong>3D地图已切换到2D</strong>
        <span>{{ threeMapFailure.message }}</span>
      </div>
      <button type="button" class="app-map-recovery__retry" @click="retryThreeMap">
        重试3D
      </button>
      <button
        type="button"
        class="app-map-recovery__close"
        title="关闭提示"
        aria-label="关闭提示"
        @click="threeMapFailure = null"
      >
        ×
      </button>
    </div>
  </div>
</template>

<style scoped>
.app-shell--dashboard {
  height: 100vh;
  overflow: hidden;
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
.app-content :deep(.left-sidebar),
.app-content :deep(.right-sidebar),
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

.app-map-recovery {
  position: fixed;
  top: 202px;
  left: 50%;
  z-index: 6;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  width: min(520px, calc(100vw - 780px));
  min-width: 390px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 190, 92, 0.58);
  border-radius: 6px;
  background: rgba(3, 18, 31, 0.94);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
  color: #d9f5ff;
  transform: translateX(-50%);
}

.app-map-recovery__status {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ffbe5c;
  box-shadow: 0 0 10px rgba(255, 190, 92, 0.72);
}

.app-map-recovery__content {
  display: grid;
  min-width: 0;
  gap: 2px;
  font-size: 12px;
  line-height: 1.45;
}

.app-map-recovery__content strong {
  color: #fff2d3;
  font-size: 13px;
}

.app-map-recovery__content span {
  overflow-wrap: anywhere;
  color: #a8c9d8;
}

.app-map-recovery__retry,
.app-map-recovery__close {
  height: 30px;
  border: 1px solid rgba(33, 230, 255, 0.46);
  border-radius: 4px;
  background: rgba(9, 49, 76, 0.9);
  color: #d9faff;
  cursor: pointer;
}

.app-map-recovery__retry {
  padding: 0 12px;
}

.app-map-recovery__close {
  width: 30px;
  padding: 0;
  font-size: 20px;
  line-height: 1;
}

.app-map-recovery__retry:hover,
.app-map-recovery__close:hover {
  border-color: #21e6ff;
  background: rgba(10, 72, 105, 0.96);
}

@media (max-width: 900px) {
  .app-content--dashboard {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }

  .app-map-recovery {
    top: 150px;
    width: auto;
    min-width: 0;
    right: 16px;
    left: 16px;
    transform: none;
  }
}
</style>
