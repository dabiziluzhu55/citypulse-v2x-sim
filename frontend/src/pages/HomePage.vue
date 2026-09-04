<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AiControlPanel from '../components/dashboard/AiControlPanel.vue'
import CenterCommunicationPanel from '../components/dashboard/CenterCommunicationPanel.vue'
import LeftSidebarPanel from '../components/dashboard/LeftSidebarPanel.vue'
import RoadsideDevicesPanel from '../components/dashboard/RoadsideDevicesPanel.vue'
import RightSidebarPanel from '../components/dashboard/RightSidebarPanel.vue'
import { useDashboardOverlay } from '../composables/useDashboardOverlay'
import { useCopilotContext } from '../composables/useCopilotContext'
import { useOptionalAppMapView } from '../composables/useAppMapView'
import { useSimulationStore } from '../composables/useSimulationStore'
import { useSnapshotMetrics } from '../composables/useSnapshotMetrics'
import { useEvaluationComparison } from '../composables/useEvaluationComparison'
import { useHealth } from '../composables/useHealth'
import { useCatalog } from '../composables/useCatalog'
import { useActiveIntersectionScene } from '../composables/useActiveIntersectionScene'
import { CESIUM_CAMERA_PRESETS } from '../constants/mapDefaults'
import type { CesiumCameraPresetId, MapDimension } from '../types/map'
import type { StartSimulationRequest } from '../types/simulation'
import { formatIntersectionLabel } from '../utils/intersectionLabels'
import { detectMap3dCapability } from '../mapv/map3dCapabilities'
import { shouldAutoPresentSimulation } from '../utils/simulationSessionState'
import { scenarioPresetIntersectionIds } from '../utils/scenarioPresetRules'

const mapView = useOptionalAppMapView()
const mapDimension = computed(() => mapView?.dimension.value ?? '2d')
const cameraPreset = computed(() => mapView?.cameraPreset.value ?? 'overview')
const cameraPresets = CESIUM_CAMERA_PRESETS
const map3dCapability = detectMap3dCapability()
const {
  activeIntersectionId,
  committedIntersectionId,
  sceneStatus,
  selectIntersection,
} = useActiveIntersectionScene()
const copilotActiveScope = computed(() => (
  activeIntersectionId.value ? `intersection:${activeIntersectionId.value}` : null
))
const {
  activeEventId: copilotActiveEventId,
  activeEventLabel: copilotActiveEventLabel,
  clearCopilotEvent,
} = useCopilotContext()
const { catalog, intersection } = useCatalog(activeIntersectionId)
const localIntersectionOptions = Array.from({ length: 20 }, (_, index) => ({
  intersection_id: `demo_${index + 1}`,
}))
const simulatableIntersectionIds = computed(() => new Set(
  catalog.value?.intersections.map((item) => item.intersection_id) ?? [],
))
const intersectionOptions = computed(() => localIntersectionOptions.map((item) => ({
  ...item,
  simulatable: simulatableIntersectionIds.value.has(item.intersection_id),
})))

function setMapDimension(next: MapDimension) {
  mapView?.setDimension(next === '3d' && !map3dCapability.supported ? '2d' : next)
}

function setCameraPreset(next: CesiumCameraPresetId) {
  if (
    sceneStatus.value !== 'ready'
    || committedIntersectionId.value !== activeIntersectionId.value
  ) return
  if (!mapView) return
  if (next === 'overview') {
    mapView.setCameraPreset(next)
    return
  }
  const selectedIntersection = intersection.value
  if (
    selectedIntersection?.longitude != null
    && selectedIntersection.latitude != null
    && Number.isFinite(selectedIntersection.longitude)
    && Number.isFinite(selectedIntersection.latitude)
  ) {
    mapView.focusIntersection(
      [selectedIntersection.longitude, selectedIntersection.latitude],
      selectedIntersection.intersection_id,
      { force: true, duration: 900, cameraPreset: next },
    )
    return
  }
  mapView.setCameraPreset(next)
}

const {
  sessionId,
  snapshot,
  state,
  starting,
  controlling,
  startError,
  controlError,
  wsConnected,
  statusError,
  restoredSession,
  sessionIntersectionId,
  activeControlMode,
  activePlaybackSpeed,
  achievedPlaybackSpeed,
  displayedOfficialTime,
  clearStatusError,
  launchRun,
  pauseRun,
  resumeRun,
  changePlaybackSpeed,
  stopRun,
  markRestoredSessionHandled,
} = useSimulationStore()
const { ready: healthReady, statusLabel: healthLabel } = useHealth()

const { logEntries } = useSnapshotMetrics(sessionId, snapshot, wsConnected)
const {
  timeseries,
  activeFingerprint,
  hasActiveComparisonData,
  finalizationWarning,
  beginRun: beginComparisonRun,
  resetForConfiguration,
} = useEvaluationComparison(sessionId, snapshot)
const {
  communicationPanelOpen,
  aiControlPanelOpen,
  roadsideDevicePanelOpen,
  sidePanelsCollapsed,
  closeCommunicationPanel,
  closeAiControlPanel,
  closeRoadsideDevicePanel,
  toggleSidePanels,
} = useDashboardOverlay()
interface ConfigurationChangeRequest {
  fingerprint: string
  differences: string[]
  apply: () => void
}
const pendingConfigChange = ref<ConfigurationChangeRequest | null>(null)
const configChangeDialogRef = ref<HTMLElement | null>(null)

const autoPresentedSessionId = ref('')

watch([snapshot, restoredSession], ([nextSnapshot, shouldRestore]) => {
  if (!shouldRestore || !nextSnapshot) return
  const intersectionId = sessionIntersectionId.value || Object.keys(nextSnapshot.intersections)[0]
  if (intersectionId) selectIntersection(intersectionId)
  markRestoredSessionHandled()
}, { immediate: true })

watch([sessionId, state], ([nextSessionId, nextState]) => {
  if (
    !nextSessionId
    || autoPresentedSessionId.value === nextSessionId
    || !nextState
    || !shouldAutoPresentSimulation(nextState)
  ) return
  autoPresentedSessionId.value = nextSessionId
  setMapDimension('3d')
}, { immediate: true })

watch(sessionId, () => clearCopilotEvent())

function handleOverlayKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (pendingConfigChange.value) {
    event.preventDefault()
    event.stopImmediatePropagation()
    cancelConfigChange()
  } else if (aiControlPanelOpen.value) {
    closeAiControlPanel()
  } else if (communicationPanelOpen.value) {
    closeCommunicationPanel()
  } else if (roadsideDevicePanelOpen.value) {
    closeRoadsideDevicePanel()
  }
}

function guardConfigChangeEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !pendingConfigChange.value) return
  event.preventDefault()
  event.stopImmediatePropagation()
  cancelConfigChange()
}

onMounted(() => {
  window.addEventListener('keydown', guardConfigChangeEscape, true)
  window.addEventListener('keydown', handleOverlayKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', guardConfigChangeEscape, true)
  window.removeEventListener('keydown', handleOverlayKeydown)
  closeAiControlPanel()
  closeCommunicationPanel()
  closeRoadsideDevicePanel()
})

async function handleStart(payload: StartSimulationRequest) {
  const frozenFocusIntersectionId = activeIntersectionId.value
  const controlledIntersectionIds = scenarioPresetIntersectionIds(
    payload.scenario_preset_id,
    catalog.value?.scenario_presets,
  )
  await launchRun(payload, frozenFocusIntersectionId, (result) => {
    beginComparisonRun(result.session_id, payload, controlledIntersectionIds)
  })
}

function handleConfigChangeRequested(request: ConfigurationChangeRequest) {
  pendingConfigChange.value = request
  void nextTick(() => configChangeDialogRef.value?.focus())
}

function cancelConfigChange() {
  pendingConfigChange.value = null
}

function confirmConfigChange() {
  const request = pendingConfigChange.value
  if (!request) return
  request.apply()
  resetForConfiguration(request.fingerprint)
  pendingConfigChange.value = null
}

async function handlePause() { await pauseRun() }
async function handleResume() { await resumeRun() }
async function handlePlaybackSpeed(value: number) { await changePlaybackSpeed(value) }

async function handleStop() {
  await stopRun()
}
</script>

<template>
  <section
    class="dashboard-page"
    :class="{ 'is-side-panels-collapsed': sidePanelsCollapsed }"
  >
    <div v-if="mapView" class="map-view-controls">
      <label class="intersection-picker">
        <span class="map-dimension-toggle__label">路口</span>
        <select
          :value="activeIntersectionId"
          aria-label="选择高精度路口"
          title="选择查看路口"
          @change="selectIntersection(($event.target as HTMLSelectElement).value)"
        >
          <option
            v-for="item in intersectionOptions"
            :key="item.intersection_id"
            :value="item.intersection_id"
          >
            {{ formatIntersectionLabel(item.intersection_id) }}
          </option>
        </select>
        <i :class="`is-${sceneStatus}`" aria-hidden="true" />
      </label>
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
          :disabled="!map3dCapability.supported"
          :title="map3dCapability.reason ?? '切换到三维地图'"
          @click="setMapDimension('3d')"
        >
          3D
        </button>
      </div>

      <button
        type="button"
        class="map-side-panel-toggle"
        :aria-expanded="!sidePanelsCollapsed"
        :title="sidePanelsCollapsed ? '展开两侧面板' : '收起两侧面板'"
        @click="toggleSidePanels"
      >
        <span aria-hidden="true">{{ sidePanelsCollapsed ? '»' : '«' }}</span>
        {{ sidePanelsCollapsed ? '展开面板' : '收起面板' }}
      </button>

      <div v-if="mapDimension === '3d'" class="map-camera-toggle" aria-label="三维地图机位视角">
        <span class="map-dimension-toggle__label">机位</span>
        <button
          v-for="preset in cameraPresets"
          :key="preset.id"
          type="button"
          class="map-dimension-toggle__btn map-camera-toggle__btn"
          :class="{ active: cameraPreset === preset.id }"
          :disabled="sceneStatus !== 'ready' || committedIntersectionId !== activeIntersectionId"
          :title="`${preset.label}：${preset.description}`"
          @click="setCameraPreset(preset.id)"
        >
          {{ preset.shortLabel }}
        </button>
      </div>
    </div>

    <div class="dashboard-column left">
      <LeftSidebarPanel
        :session-id="sessionId"
        :state="state"
        :snapshot="snapshot"
        :starting="starting"
        :controlling="controlling"
        :start-error="startError"
        :control-error="controlError"
        :status-error="statusError"
        :ws-connected="wsConnected"
        :active-control-mode="activeControlMode"
        :active-playback-speed="activePlaybackSpeed"
        :achieved-playback-speed="achievedPlaybackSpeed"
        :displayed-official-time="displayedOfficialTime"
        :active-comparison-fingerprint="activeFingerprint"
        :has-active-comparison-data="hasActiveComparisonData"
        :health-ready="healthReady"
        :health-label="healthLabel"
        @start="handleStart"
        @pause="handlePause"
        @resume="handleResume"
        @playback-speed="handlePlaybackSpeed"
        @stop="handleStop"
        @dismiss-status-error="clearStatusError"
        @config-change-requested="handleConfigChangeRequested"
      />
    </div>

    <div class="dashboard-column center" />

    <Teleport to="body">
      <Transition name="config-notice">
        <div v-if="pendingConfigChange" class="config-change-dialog">
        <button
          type="button"
          class="config-change-dialog__backdrop"
          aria-label="取消参数变更"
          @click="cancelConfigChange"
        />
        <div
          ref="configChangeDialogRef"
          class="config-change-notice"
          role="dialog"
          aria-modal="true"
          aria-labelledby="config-change-title"
          tabindex="-1"
        >
          <div id="config-change-title" class="config-change-notice__title"><i aria-hidden="true" />参数变更确认</div>
          <p>以下仿真参数会改变评估基准。确认后切换到对应实验组，原有算法曲线仍会保留。</p>
          <ul>
            <li v-for="difference in pendingConfigChange.differences" :key="difference">
              {{ difference }}
            </li>
          </ul>
          <div class="config-change-notice__actions">
            <button type="button" @click="cancelConfigChange">取消</button>
            <button type="button" class="is-primary" @click="confirmConfigChange">确认切换</button>
          </div>
        </div>
        </div>
      </Transition>
    </Teleport>

    <Transition name="communication-overlay">
      <div
        v-if="aiControlPanelOpen"
        id="ai-control-dialog"
        class="communication-overlay ai-control-overlay"
        role="region"
        aria-label="CityPulse-Qwen AI交通助手"
      >
        <div class="communication-overlay__panel ai-control-overlay__panel">
          <AiControlPanel
            :session-id="sessionId"
            :active-event-id="copilotActiveEventId"
            :active-event-label="copilotActiveEventLabel"
            :active-scope="copilotActiveScope"
            :ai-takeover="snapshot?.ai_takeover ?? null"
            @close="closeAiControlPanel"
          />
        </div>
      </div>
    </Transition>

    <Transition name="communication-overlay">
      <div
        v-if="communicationPanelOpen"
        id="center-communication-dialog"
        class="communication-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="车路云通信记录"
      >
        <button
          type="button"
          class="communication-overlay__backdrop"
          aria-label="关闭车路云通信记录"
          @click="closeCommunicationPanel"
        />
        <div class="communication-overlay__panel communication-overlay__panel--communication">
          <CenterCommunicationPanel
            :log-entries="logEntries"
            :loading="false"
            :error="null"
            :connected="wsConnected"
            @close="closeCommunicationPanel"
          />
        </div>
      </div>
    </Transition>

    <Transition name="communication-overlay">
      <div
        v-if="roadsideDevicePanelOpen"
        id="roadside-device-dialog"
        class="communication-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="路侧设备"
      >
        <button
          type="button"
          class="communication-overlay__backdrop"
          aria-label="关闭路侧设备"
          @click="closeRoadsideDevicePanel"
        />
        <div class="communication-overlay__panel">
          <RoadsideDevicesPanel
            :intersection-id="activeIntersectionId"
            :runtime="snapshot?.intersections?.[activeIntersectionId] ?? null"
            @close="closeRoadsideDevicePanel"
          />
        </div>
      </div>
    </Transition>

    <div class="dashboard-column right">
      <RightSidebarPanel
        :run-id="sessionId"
        :active-algorithm="snapshot?.evaluation?.algorithm ?? snapshot?.metrics.evaluation?.algorithm ?? activeControlMode"
        :log-entries="logEntries"
        :collaboration-loading="false"
        :collaboration-error="null"
        :ws-connected="wsConnected"
        :timeseries="timeseries"
        :timeseries-loading="false"
        :timeseries-error="finalizationWarning"
      />
    </div>
  </section>
</template>

<style scoped>
.config-change-dialog {
  position: fixed;
  inset: 0;
  z-index: 3100;
  display: grid;
  place-items: center;
  padding: 24px;
}
.config-change-dialog__backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  background: rgba(1, 10, 24, .48);
  cursor: default;
}
.config-change-notice {
  position: relative;
  width: min(345px, calc(100vw - 32px));
  min-height: 141px;
  padding: 24px 30px 22px;
  border: 1px solid rgba(49, 173, 255, .72);
  clip-path: polygon(12px 0, calc(100% - 12px) 0, 100% 12px, 100% calc(100% - 12px), calc(100% - 12px) 100%, 12px 100%, 0 calc(100% - 12px), 0 12px);
  background: linear-gradient(180deg, rgba(6, 43, 82, .98), rgba(2, 19, 44, .98));
  box-shadow: inset 0 0 30px rgba(33, 139, 255, .12), 0 0 22px rgba(33, 139, 255, .28);
  color: #edf9ff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  pointer-events: auto;
}
.config-change-notice::before,
.config-change-notice::after {
  content: '';
  position: absolute;
  top: 0;
  width: 95px;
  height: 2px;
  background: linear-gradient(90deg, transparent, #52c2fa);
  box-shadow: 0 0 8px #21e6ff;
}
.config-change-notice::before { left: 0; }
.config-change-notice::after { right: 0; transform: scaleX(-1); }
.config-change-notice__title { display: flex; align-items: center; gap: 10px; color: #fff; font-size: 18px; font-weight: 700; }
.config-change-notice__title i { width: 5px; height: 18px; background: #21e6ff; box-shadow: 0 0 8px #21e6ff; }
.config-change-notice p { margin: 18px 0 0; color: #bcdced; font-size: 14px; line-height: 1.6; text-align: center; }
.config-change-notice__actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 22px; }
.config-change-notice__actions button {
  min-width: 92px; height: 34px; border: 1px solid rgba(82,194,250,.55); border-radius: 4px;
  background: rgba(4,49,91,.82); color: #d7efff; font: 600 13px/1 inherit; cursor: pointer;
}
.config-change-notice__actions button:hover, .config-change-notice__actions button:focus-visible { border-color: #52c2fa; outline: none; filter: brightness(1.14); }
.config-change-notice__actions button.is-primary { background: linear-gradient(180deg,#2e519e,#3c8de7); color: #fff; }
.config-notice-enter-active,
.config-notice-leave-active { transition: opacity .18s ease; }
.config-notice-enter-active .config-change-notice,
.config-notice-leave-active .config-change-notice { transition: opacity .18s ease, transform .18s ease; }
.config-notice-enter-from,
.config-notice-leave-to { opacity: 0; }
.config-notice-enter-from .config-change-notice,
.config-notice-leave-to .config-change-notice { opacity: 0; transform: translateY(12px) scale(.97); }

.communication-overlay {
  position: fixed;
  inset: var(--dashboard-top-offset) 0 var(--dashboard-bottom-offset);
  z-index: 9;
  display: grid;
  place-items: center;
  padding: 24px;
  pointer-events: none;
}

.communication-overlay__backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  background: rgba(1, 10, 24, .34);
  cursor: default;
  pointer-events: auto;
}

.communication-overlay__panel {
  position: relative;
  z-index: 1;
  width: min(1000px, 100%);
  pointer-events: auto;
}

.communication-overlay__panel--communication {
  width: min(1490px, 100%);
}

.communication-overlay-enter-active,
.communication-overlay-leave-active {
  transition: opacity 0.22s ease;
}

.communication-overlay-enter-active .communication-overlay__panel,
.communication-overlay-leave-active .communication-overlay__panel {
  transition: opacity 0.22s ease, transform 0.22s ease;
}

.communication-overlay-enter-from,
.communication-overlay-leave-to {
  opacity: 0;
}

.communication-overlay-enter-from .communication-overlay__panel,
.communication-overlay-leave-to .communication-overlay__panel {
  opacity: 0;
  transform: translateY(18px) scale(0.97);
}

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

.map-side-panel-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 42px;
  padding: 0 15px;
  border: 1px solid rgba(0, 255, 255, .22);
  border-radius: 999px;
  background: rgba(2, 10, 24, .82);
  backdrop-filter: blur(10px);
  color: var(--cp-text-secondary);
  font: 600 12px/1 'PingFang SC', 'Microsoft YaHei', sans-serif;
  letter-spacing: .04em;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color .2s ease, box-shadow .2s ease, color .2s ease, background .2s ease;
}

.map-side-panel-toggle > span {
  color: #21e6ff;
  font-size: 16px;
  line-height: 1;
}

.map-side-panel-toggle:hover,
.map-side-panel-toggle:focus-visible {
  border-color: var(--cp-accent);
  background: rgba(33, 230, 255, .08);
  box-shadow: var(--cp-glow);
  color: var(--cp-accent);
  outline: none;
}

.ai-control-overlay {
  /* 10px toolbar offset + 42px control height + 20px visual clearance. */
  top: calc(var(--dashboard-top-offset) + 72px);
  right: calc(var(--dashboard-panel-inset-right) + var(--dashboard-right-width) + 18px);
  bottom: calc(var(--dashboard-bottom-offset) + 34px);
  left: calc(var(--dashboard-panel-inset-left) + var(--dashboard-left-width) + 18px);
  padding: 0;
  place-items: stretch;
  background: transparent;
}

.ai-control-overlay__panel {
  display: block;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  pointer-events: none;
}

.dashboard-page.is-side-panels-collapsed .ai-control-overlay {
  right: 48px;
  left: 48px;
}

.map-dimension-toggle,
.map-camera-toggle,
.intersection-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid rgba(0, 255, 255, 0.22);
  border-radius: 999px;
  background: rgba(2, 10, 24, 0.82);
  backdrop-filter: blur(10px);
}

.intersection-picker select {
  min-width: 92px;
  border: 0;
  outline: 0;
  background: transparent;
  color: #d8f5ff;
  font: inherit;
  cursor: pointer;
}

.intersection-picker select option {
  background: #07182a;
  color: #d8f5ff;
}

.intersection-picker i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7f8d99;
}

.intersection-picker i.is-loading {
  background: #e8b94c;
}

.intersection-picker i.is-ready {
  background: #3ce69a;
  box-shadow: 0 0 7px rgba(60, 230, 154, 0.72);
}

.intersection-picker i.is-error {
  background: #ff6b6b;
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

@media (max-width: 1320px) {
  .communication-overlay {
    inset: var(--dashboard-top-offset) 0 80px;
    padding: 20px;
    place-items: center;
  }

  .ai-control-overlay {
    top: calc(var(--dashboard-top-offset) + 72px);
    right: 20px;
    bottom: 116px;
    left: 20px;
    padding: 0;
    place-items: stretch;
  }
}

@media (prefers-reduced-motion: reduce) {
  .communication-overlay-enter-active,
  .communication-overlay-leave-active,
  .communication-overlay-enter-active .communication-overlay__panel,
  .communication-overlay-leave-active .communication-overlay__panel {
    transition: none;
  }
}

@media (max-width: 1100px) {
  .dashboard-page {
    top: calc(var(--dashboard-top-offset) + 116px);
  }

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
