<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LeftSidebarPanel from '../components/dashboard/LeftSidebarPanel.vue'
import RightSidebarPanel from '../components/dashboard/RightSidebarPanel.vue'
import { useOptionalAppMapView } from '../composables/useAppMapView'
import { useRunOverview } from '../composables/useRunOverview'
import { useRunStatusPolling } from '../composables/useRunStatusPolling'
import { useSimulationRun } from '../composables/useSimulationRun'
import { useTrafficState } from '../composables/useTrafficState'
import { useCollaborationState } from '../composables/useCollaborationState'
import { useAlgorithmControl } from '../composables/useAlgorithmControl'
import { useEventsAndPrediction } from '../composables/useEventsAndPrediction'
import { useMetricsDisplay } from '../composables/useMetricsDisplay'
import { CESIUM_CAMERA_PRESETS } from '../constants/mapDefaults'
import type { CesiumCameraPresetId, MapDimension } from '../types/map'
import type { ControlCommand } from '../types/simulation'

const route = useRoute()
const router = useRouter()

const initialScenarioId = computed(() => {
  const value = route.query.scenario_id
  return typeof value === 'string' ? value : ''
})

const mapView = useOptionalAppMapView()
const mapDimension = computed(() => mapView?.dimension.value ?? '2d')
const cameraPreset = computed(() => mapView?.cameraPreset.value ?? 'overview')
const cameraPresets = CESIUM_CAMERA_PRESETS

function setMapDimension(next: MapDimension) {
  mapView?.setDimension(next)
}

function setCameraPreset(next: CesiumCameraPresetId) {
  mapView?.setCameraPreset(next)
}

const selectedTemplateId = ref('')

function handleTemplateIdChange(templateId: string) {
  selectedTemplateId.value = templateId
}

watch(
  initialScenarioId,
  (scenarioId) => {
    if (!mapView || !scenarioId) {
      return
    }
    mapView.flyToScenario(scenarioId)
  },
  { immediate: true },
)

const {
  runId,
  starting,
  controlling,
  startError,
  controlError,
  launchRun,
  sendControl,
} = useSimulationRun()

const { overview } = useRunOverview(runId)
const {
  status: runStatus,
  refresh: refreshStatus,
} = useRunStatusPolling(runId)

const { refresh: refreshTraffic } = useTrafficState(runId)

const {
  collaborationState,
  logEntries,
  loading: collaborationLoading,
  error: collaborationError,
  wsConnected: collaborationWsConnected,
  refresh: refreshCollaboration,
} = useCollaborationState(runId)

const { selectedAlgorithmId } = useAlgorithmControl(runId, overview, collaborationState)

const { refreshAll: refreshEventsAndPrediction } = useEventsAndPrediction(runId)

const experimentId = computed(() => {
  const queryValue = route.query.experiment_id
  if (typeof queryValue === 'string' && queryValue.trim()) {
    return queryValue.trim()
  }
  return overview.value?.scenario_id ?? import.meta.env.VITE_DEFAULT_EXPERIMENT_ID ?? ''
})

const currentAlgorithmId = computed(() => overview.value?.algorithm ?? selectedAlgorithmId.value)

const {
  timeseries,
  timeseriesLoading,
  timeseriesError,
  refreshAll: refreshMetrics,
} = useMetricsDisplay(runId, experimentId, currentAlgorithmId)

const controlStatus = computed(() => runStatus.value?.status ?? null)

async function handleStartFromScenario(scenarioId: string) {
  const result = await launchRun({
    scenario_id: scenarioId,
    algorithm: selectedAlgorithmId.value || 'fixed_time',
    cloud_edge_enabled: true,
    realtime: true,
    step_length: 1.0,
  })
  if (result) {
    refreshStatus()
    refreshTraffic()
    refreshCollaboration()
    refreshEventsAndPrediction()
    refreshMetrics()
  }
}

function handleScenarioGenerated(scenarioId: string) {
  void router.replace({
    query: {
      ...route.query,
      scenario_id: scenarioId,
    },
  })
}

async function handleControl(command: ControlCommand) {
  const result = await sendControl(command)
  if (result) {
    refreshStatus()
    refreshTraffic()
    refreshCollaboration()
    refreshEventsAndPrediction()
    refreshMetrics()
  }
}
</script>

<template>
  <section class="dashboard-page">
    <div v-if="mapView" class="map-view-controls">
      <div class="map-dimension-toggle">
        <span class="map-dimension-toggle__label">地图视图</span>
        <button
          type="button"
          class="map-dimension-toggle__btn"
          :class="{ active: mapDimension === '2d' }"
          @click="setMapDimension('2d')"
        >
          2D
        </button>
        <button
          type="button"
          class="map-dimension-toggle__btn"
          :class="{ active: mapDimension === '3d' }"
          @click="setMapDimension('3d')"
        >
          3D
        </button>
      </div>

      <div v-if="mapDimension === '3d'" class="map-camera-toggle" aria-label="三维地图机位视角">
        <span class="map-dimension-toggle__label">机位</span>
        <button
          v-for="preset in cameraPresets"
          :key="preset.id"
          type="button"
          class="map-dimension-toggle__btn map-camera-toggle__btn"
          :class="{ active: cameraPreset === preset.id }"
          :title="`${preset.label}：${preset.description}`"
          @click="setCameraPreset(preset.id)"
        >
          {{ preset.shortLabel }}
        </button>
      </div>
    </div>

    <div class="dashboard-column left">
      <LeftSidebarPanel
        :run-id="runId"
        :status="controlStatus"
        :run-status="runStatus"
        :starting="starting"
        :controlling="controlling"
        :start-error="startError"
        :control-error="controlError"
        :initial-scenario-id="initialScenarioId"
        :selected-template-id="selectedTemplateId"
        v-model:selected-algorithm-id="selectedAlgorithmId"
        @generate="handleScenarioGenerated"
        @start="handleStartFromScenario"
        @control="handleControl"
        @update:template-id="handleTemplateIdChange"
      />
    </div>

    <div class="dashboard-column center" aria-hidden="true" />

    <div class="dashboard-column right">
      <RightSidebarPanel
        :run-id="runId"
        :log-entries="logEntries"
        :collaboration-loading="collaborationLoading"
        :collaboration-error="collaborationError"
        :ws-connected="collaborationWsConnected"
        :timeseries="timeseries"
        :timeseries-loading="timeseriesLoading"
        :timeseries-error="timeseriesError"
        @refresh="refreshMetrics"
      />
    </div>
  </section>
</template>

<style scoped>
.map-view-controls {
  position: fixed;
  top: calc(var(--dashboard-top-offset) + 10px);
  left: 50%;
  transform: translateX(-50%);
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 10px;
  pointer-events: auto;
}

.map-dimension-toggle,
.map-camera-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid rgba(0, 255, 255, 0.22);
  border-radius: 999px;
  background: rgba(2, 10, 24, 0.82);
  backdrop-filter: blur(10px);
}

.map-camera-toggle {
  animation: camera-panel-enter 0.2s ease-out;
}

.map-dimension-toggle__label {
  color: var(--cp-text-secondary);
  font-size: 12px;
  letter-spacing: 0.08em;
  white-space: nowrap;
}

.map-dimension-toggle__btn {
  padding: 6px 14px;
  border: 1px solid rgba(0, 255, 255, 0.18);
  border-radius: 999px;
  background: transparent;
  color: var(--cp-text-secondary);
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, color 0.2s ease, background 0.2s ease;
}

.map-camera-toggle__btn {
  padding-inline: 12px;
}

.map-dimension-toggle__btn:hover,
.map-dimension-toggle__btn.active {
  border-color: var(--cp-accent);
  background: rgba(33, 230, 255, 0.08);
  box-shadow: var(--cp-glow);
  color: var(--cp-accent);
}

@keyframes camera-panel-enter {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1100px) {
  .map-view-controls {
    flex-direction: column;
    top: calc(var(--dashboard-top-offset) + 6px);
  }

  .map-camera-toggle {
    max-width: calc(100vw - 48px);
    overflow-x: auto;
  }
}
</style>
