<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { DISTURBANCE_CHOICE_OPTIONS, TRAFFIC_FLOW_MODE_OPTIONS } from '../../constants/scenarioOptions'
import { resolveControlModeLabel, resolveDashboardControlModes } from '../../constants/simulationOptions'
import { exportScenarioArchive } from '../../api/scenario'
import { useCompactScenarioConfig } from '../../composables/useCompactScenarioConfig'
import { useCatalog } from '../../composables/useCatalog'
import { useActiveIntersectionScene } from '../../composables/useActiveIntersectionScene'
import {
  catalogSupportsScenarioPreset,
  catalogSupportsScenarioPresetForIntersection,
  findCatalogScenarioPreset,
  findRunnableScenarioPreset,
  missingPresetIntersectionIds,
} from '../../composables/catalogCapabilities'
import LeftSidebarFrameSvg from './LeftSidebarFrameSvg.vue'
import LeftSidebarBottomChrome from './LeftSidebarBottomChrome.vue'
import LeftSidebarSectionHeader from './LeftSidebarSectionHeader.vue'
import { LEFT_SIDEBAR_DESIGN_HEIGHT, LEFT_SIDEBAR_DESIGN_WIDTH, LEFT_SIDEBAR_REFERENCE_LAYOUT } from '../../constants/leftSidebarLayout'
import type { SimulationSnapshot, SimulationState, StartSimulationRequest } from '../../types/simulation'

const props = defineProps<{
  sessionId: string
  state: SimulationState | null
  snapshot: SimulationSnapshot | null
  starting: boolean
  controlling: boolean
  startError: string | null
  controlError: string | null
  statusError: string | null
  wsConnected: boolean
  activeControlMode: string
  activePlaybackSpeed: number
  healthReady: boolean
  healthLabel: string
}>()
const emit = defineEmits<{
  start: [payload: StartSimulationRequest]
  pause: []
  resume: []
  stop: []
  playbackSpeed: [value: number]
  configChanged: []
}>()

const { activeIntersectionId, selectIntersection } = useActiveIntersectionScene()
const {
  catalog,
  intersection,
  periods,
  controlModes,
  scenarioPresets,
  playbackSpeeds,
  loading: catalogLoading,
  error: catalogError,
  isIntersectionSupported,
} = useCatalog(activeIntersectionId)
const { config, configNote, availableTimeOptions, buildPayload, applyImportedConfig } = useCompactScenarioConfig(
  intersection,
  periods,
  scenarioPresets,
  playbackSpeeds,
)
const fileInput = ref<HTMLInputElement | null>(null)
const feedback = ref<string | null>(null)
const multiplierOpen = ref(false)
const exporting = ref(false)
const scenarioOptions = computed(() => scenarioPresets.value.map((item) => ({
  label: item.label,
  value: item.preset_id,
  disabled: !catalogSupportsScenarioPreset(catalog.value, item.preset_id),
})))
const fields = computed(() => [
  { key: 'scenario', label: '场景模式', options: scenarioOptions.value },
  { key: 'disturbance', label: '扰动事件', options: DISTURBANCE_CHOICE_OPTIONS },
  { key: 'flow', label: '交通流模式', options: TRAFFIC_FLOW_MODE_OPTIONS },
  { key: 'time', label: '仿真时间', options: availableTimeOptions.value },
])
const isSessionActive = computed(() => props.starting || (
  !!props.sessionId && (!props.state || ['STARTING', 'RUNNING', 'PAUSED', 'STOPPING'].includes(props.state))
))
const canStop = computed(() => isSessionActive.value)
const canPause = computed(() => props.state === 'RUNNING' && !props.controlling)
const canResume = computed(() => props.state === 'PAUSED' && !props.controlling)
const selectedPreset = computed(() => findCatalogScenarioPreset(catalog.value, config.value.scenario_preset_id))
const presetMissingIds = computed(() => missingPresetIntersectionIds(catalog.value, config.value.scenario_preset_id))
const presetUnavailableMessage = computed(() => {
  if (catalogLoading.value) return ''
  if (!selectedPreset.value) return '后端未提供当前场景预设'
  if (presetMissingIds.value.length > 0) {
    return `后端20路口 manifest 不完整，缺少 ${presetMissingIds.value.length} 个路口`
  }
  if (!selectedPreset.value.intersection_ids.includes(activeIntersectionId.value)) {
    return `${activeIntersectionId.value} 不属于当前仿真场景，请先切换场景内路口`
  }
  return ''
})
const canStart = computed(() => (
  props.healthReady
  && !catalogLoading.value
  && isIntersectionSupported.value
  && !presetUnavailableMessage.value
  && !props.starting
  && !isSessionActive.value
))
const progressPercent = computed(() => typeof props.snapshot?.progress === 'number' ? Math.min(100, Math.max(0, props.snapshot.progress * 100)) : 0)
const unsupportedMessage = computed(() => (
  !catalogLoading.value && !isIntersectionSupported.value
    ? `${activeIntersectionId.value} 当前仅支持高精度查看，尚未接入真实仿真路网`
    : ''
))
const statusMessage = computed(() => feedback.value
  || props.startError
  || props.controlError
  || props.statusError
  || catalogError.value
  || unsupportedMessage.value
  || presetUnavailableMessage.value
  || (!props.healthReady ? props.healthLabel : ''))
const controlModeOptions = computed(() => resolveDashboardControlModes(controlModes.value))
const playbackSpeedOptions = computed(() => playbackSpeeds.value.map((value) => ({ label: `${value}x`, value })))
const stateLabel = computed(() => props.state ?? (props.healthReady ? 'READY' : 'OFFLINE'))
const connectionLabel = computed(() => {
  if (!props.sessionId) return '未连接'
  return props.wsConnected ? '实时推送' : '轮询同步'
})
const officialTimeLabel = computed(() => {
  const value = props.snapshot?.official_time
  if (!value) return '--:--:--'
  const time = value.includes('T') ? value.split('T')[1] : value
  return time.slice(0, 8)
})
const activeVehicleLabel = computed(() => props.snapshot?.metrics.active_vehicles ?? 0)
const sequenceLabel = computed(() => props.snapshot?.sequence ?? 0)
const algorithmLabel = computed(() => resolveControlModeLabel(
  props.activeControlMode || config.value.control_mode,
))
const startTitle = computed(() => {
  if (catalogLoading.value) return '正在读取真实仿真路口目录'
  if (unsupportedMessage.value) return unsupportedMessage.value
  if (presetUnavailableMessage.value) return presetUnavailableMessage.value
  if (!props.healthReady) return props.healthLabel
  if (isSessionActive.value) return '当前仿真尚未结束'
  return '开始仿真'
})

watch(
  [catalog, activeIntersectionId, () => config.value.scenario_preset_id],
  ([nextCatalog, intersectionId, presetId]) => {
    if (catalogSupportsScenarioPresetForIntersection(nextCatalog, presetId, intersectionId)) return
    const fallback = findRunnableScenarioPreset(nextCatalog, intersectionId)
    if (fallback) config.value.scenario_preset_id = fallback.preset_id
  },
  { immediate: true },
)

watch(
  () => props.activePlaybackSpeed,
  (value) => {
    if (isSessionActive.value && playbackSpeeds.value.includes(value)) config.value.playback_speed = value
  },
  { immediate: true },
)

watch(
  [() => props.activeControlMode, controlModeOptions, isSessionActive],
  ([value, options, active]) => {
    if (
      active
      && value
      && options.some((option) => option.value === value)
    ) config.value.control_mode = value
  },
  { immediate: true },
)

watch(
  () => config.value.scenario_preset_id,
  (presetId) => {
    const preset = findCatalogScenarioPreset(catalog.value, presetId)
    if (
      preset
      && catalogSupportsScenarioPreset(catalog.value, presetId)
      && !preset.intersection_ids.includes(activeIntersectionId.value)
    ) {
      selectIntersection(preset.intersection_ids[0])
    }
  },
)

function fieldModel(key: string): 'scenario_preset_id' | 'disturbance' | 'flow_mode' | 'time_preset' {
  if (key === 'scenario') return 'scenario_preset_id'
  if (key === 'disturbance') return 'disturbance'
  if (key === 'flow') return 'flow_mode'
  return 'time_preset'
}
function notifyConfigChanged() { emit('configChanged') }
function openFilePicker() { fileInput.value?.click() }
async function importConfig(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    applyImportedConfig(JSON.parse(await file.text()))
    feedback.value = '配置参数已载入'
    notifyConfigChanged()
  }
  catch (error) { feedback.value = error instanceof Error ? error.message : '配置导入失败' }
  finally { input.value = '' }
}
async function exportConfig() {
  if (presetUnavailableMessage.value) {
    feedback.value = presetUnavailableMessage.value
    return
  }
  exporting.value = true
  try {
    const { blob, filename } = await exportScenarioArchive(buildPayload())
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename ?? `${config.value.scenario_preset_id}-${Date.now()}.zip`
    link.click()
    URL.revokeObjectURL(url)
    feedback.value = 'SUMO仿真场景ZIP已导出'
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '场景导出失败'
  } finally {
    exporting.value = false
  }
}
function handleStart() {
  multiplierOpen.value = false
  if (!isIntersectionSupported.value) {
    feedback.value = unsupportedMessage.value
    return
  }
  if (presetUnavailableMessage.value) {
    feedback.value = presetUnavailableMessage.value
    return
  }
  try {
    emit('start', buildPayload())
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '无法构造仿真请求'
  }
}
function selectPlaybackSpeed(value: number) {
  if (props.controlling) return
  config.value.playback_speed = value
  multiplierOpen.value = false
  if (props.state === 'RUNNING' || props.state === 'PAUSED') emit('playbackSpeed', value)
}
function handleMultiplierKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') multiplierOpen.value = false
}
</script>

<template>
  <section class="left-sidebar" aria-label="左侧数据面板">
    <div
      class="left-sidebar__canvas"
      :style="{
        width: `${LEFT_SIDEBAR_DESIGN_WIDTH}px`,
        height: `${LEFT_SIDEBAR_DESIGN_HEIGHT}px`,
      }"
    >
      <LeftSidebarFrameSvg class="left-sidebar__frame" />
      <LeftSidebarSectionHeader title="仿真场景配置" variant="scenario" />

      <button v-if="statusMessage" type="button" class="left-sidebar__status-dot" :class="{ 'is-feedback': feedback }" :title="statusMessage" :aria-label="statusMessage" @click="feedback = null" />

      <label
        v-for="(field, index) in fields"
        :key="field.key"
        class="left-sidebar__field"
        :style="{ left: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[index].left}px`, top: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[index].top}px`, width: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[index].width}px` }"
      >
        <span class="left-sidebar__field-label">{{ field.label }}</span>
        <el-select v-model="config[fieldModel(field.key)]" :disabled="isSessionActive" class="left-sidebar__select" popper-class="left-sidebar-select-popper" @change="notifyConfigChanged">
          <el-option
            v-for="option in field.options"
            :key="option.value"
            :label="option.label"
            :value="option.value"
            :disabled="'disabled' in option && option.disabled"
          />
        </el-select>
      </label>

      <div class="left-sidebar__config-summary" :title="configNote">
        <span v-for="line in configNote.split('\n')" :key="line">{{ line }}</span>
      </div>

      <div class="left-sidebar__file-actions">
        <input ref="fileInput" type="file" accept="application/json,.json" @change="importConfig" />
        <button type="button" @click="openFilePicker">上传配置参数</button>
        <button type="button" :disabled="exporting || !!presetUnavailableMessage" @click="exportConfig">{{ exporting ? '导出中…' : '导出当前仿真场景' }}</button>
      </div>

      <LeftSidebarSectionHeader title="管控算法选择" variant="algorithm" />
      <div class="left-sidebar__algorithm-list" role="radiogroup" aria-label="管控算法选择">
        <label
          v-for="(option, index) in controlModeOptions"
          :key="option.value"
          class="left-sidebar__algorithm-item"
          :class="{ 'is-selected': config.control_mode === option.value }"
          :style="{ top: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.algorithmItems.top + index * (LEFT_SIDEBAR_REFERENCE_LAYOUT.algorithmItems.height + LEFT_SIDEBAR_REFERENCE_LAYOUT.algorithmItems.gap)}px` }"
          title="真实后端算法"
        >
          <span>{{ option.label }}</span>
          <input v-model="config.control_mode" :disabled="isSessionActive" type="radio" name="sidebar-algorithm" :value="option.value" @change="notifyConfigChanged" /><i aria-hidden="true" />
        </label>
      </div>

      <LeftSidebarBottomChrome />
      <div class="left-sidebar__progress" role="progressbar" :aria-valuenow="Math.round(progressPercent)" aria-valuemin="0" aria-valuemax="100">
        <div class="left-sidebar__progress-fill" :style="{ width: `${progressPercent}%` }" />
        <span class="left-sidebar__progress-knob" :style="{ left: `${progressPercent}%` }" />
      </div>
      <div class="left-sidebar__multiplier" @keydown="handleMultiplierKeydown">
        <el-dropdown
          trigger="click"
          placement="top-end"
          :teleported="true"
          popper-class="left-sidebar-speed-popper"
          @command="selectPlaybackSpeed"
          @visible-change="multiplierOpen = $event"
        >
          <button
            type="button"
            class="left-sidebar__speed-badge"
            :class="{ 'is-open': multiplierOpen }"
            :disabled="props.controlling"
            :aria-expanded="multiplierOpen"
            aria-haspopup="listbox"
            title="选择仿真播放倍速"
          >×{{ config.playback_speed }}</button>
          <template #dropdown>
            <el-dropdown-menu aria-label="仿真播放倍速">
              <el-dropdown-item
                v-for="option in playbackSpeedOptions"
                :key="option.value"
                :command="option.value"
                :class="{ 'is-selected': config.playback_speed === option.value }"
              >×{{ option.value }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <div class="left-sidebar__controls">
        <button type="button" :disabled="!canStart" :title="startTitle" @click="handleStart">{{ starting ? '启动中…' : '开始仿真' }}</button>
        <button
          type="button"
          :disabled="!canPause && !canResume"
          :title="canResume ? '恢复仿真' : '暂停仿真'"
          @click="canResume ? emit('resume') : emit('pause')"
        >{{ props.state === 'PAUSED' ? '继续仿真' : '暂停仿真' }}</button>
        <button type="button" :disabled="!sessionId || !canStop || controlling" @click="emit('stop')">结束仿真</button>
      </div>

      <div class="left-sidebar__runtime" :class="`is-${state?.toLowerCase() ?? 'ready'}`" aria-live="polite">
        <div class="left-sidebar__runtime-head">
          <strong><i aria-hidden="true" />{{ stateLabel }}</strong>
          <span>{{ connectionLabel }}</span>
          <em>{{ algorithmLabel }}</em>
        </div>
        <dl>
          <div><dt>仿真时间</dt><dd>{{ officialTimeLabel }}</dd></div>
          <div><dt>活动车辆</dt><dd>{{ activeVehicleLabel }}</dd></div>
          <div><dt>快照序号</dt><dd>{{ sequenceLabel }}</dd></div>
          <div><dt>运行进度</dt><dd>{{ Math.round(progressPercent) }}%</dd></div>
        </dl>
      </div>
    </div>
  </section>
</template>

<style scoped>
.left-sidebar {
  container-type: size;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  pointer-events: auto;
}

.left-sidebar__canvas {
  --ls-scale: min(1, calc(100cqw / 439px), calc(100cqh / 870px));
  position: relative;
  color: #fff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  transform: scale(var(--ls-scale));
  transform-origin: top left;
}

.left-sidebar__frame {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}

.left-sidebar__status {
  position: absolute;
  z-index: 7;
  top: 84px;
  left: 29px;
  width: 357px;
  height: 24px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 10px;
  border: 1px solid rgba(255, 180, 88, 0.32);
  border-radius: 4px;
  background: rgba(42, 27, 16, 0.82);
  color: #ffd59a;
  font-size: 11px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.left-sidebar__status-dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ffb458;
  box-shadow: 0 0 7px #ffb458;
}

.left-sidebar__field {
  position: absolute;
  z-index: 3;
  width: 164px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.left-sidebar__field--1 { left: 28px; top: 96px; }
.left-sidebar__field--2 { left: 211px; top: 96px; }
.left-sidebar__field--3 { left: 28px; top: 180px; }
.left-sidebar__field--4 { left: 211px; top: 180px; }
.left-sidebar__field--5 { left: 28px; top: 264px; width: 347px; }

.left-sidebar__field-label {
  height: 19px;
  color: #accde6;
  font-size: 15px;
  line-height: 19px;
}

.left-sidebar__select { width: 100%; }
.left-sidebar__select :deep(.el-select__wrapper) {
  min-height: 37px;
  border: 1px solid rgba(27, 126, 242, 0.32);
  border-radius: 5px;
  background: linear-gradient(90deg, #043563, #03315b);
  box-shadow: inset 0 0 10px rgba(33, 230, 255, 0.04);
}
.left-sidebar__select :deep(.el-select__selected-item),
.left-sidebar__select :deep(.el-select__placeholder) { color: #fff; font-size: 15px; }
.left-sidebar__select :deep(.el-select__caret) { display: none; }
.left-sidebar__select :deep(.el-select__suffix)::after {
  content: '';
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 8px solid #ffe47a;
}

.left-sidebar__config-summary {
  position: absolute;
  z-index: 3;
  left: 28px;
  top: 342px;
  width: 358px;
  height: 53px;
  display: grid;
  grid-template-columns: 66px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  padding: 8px 15px;
  border: 1px solid rgba(0, 102, 255, 0.5);
  border-radius: 26px;
  background: linear-gradient(180deg, rgba(4, 49, 91, 0.86), rgba(2, 24, 54, 0.76));
  box-shadow: inset 0 -1px 0 rgba(206, 240, 255, 0.35), 0 0 12px rgba(0, 102, 255, 0.12);
}

.left-sidebar__summary-kicker { color: #8ec8ef; font-size: 12px; letter-spacing: 0.08em; }
.left-sidebar__config-summary p {
  margin: 0;
  overflow: hidden;
  color: #e0f0ff;
  font-size: 12px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.left-sidebar__algorithm-item {
  position: absolute;
  z-index: 3;
  left: 31px;
  width: 356px;
  height: 36px;
  display: flex;
  align-items: center;
  padding: 0 46px 0 18px;
  border: 1px solid rgba(27, 126, 242, 0.3);
  border-radius: 5px;
  background: linear-gradient(90deg, #043563, #03315b);
  color: #fff;
  font-size: 15px;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.left-sidebar__algorithm-item input { position: absolute; opacity: 0; }
.left-sidebar__algorithm-item i {
  position: absolute;
  right: 14px;
  width: 20px;
  height: 20px;
  border: 1px solid #1b7ef2;
  border-radius: 50%;
  background: #161616;
}
.left-sidebar__algorithm-item i::after {
  content: '';
  position: absolute;
  inset: 7px;
  border-radius: 50%;
  background: transparent;
}
.left-sidebar__algorithm-item.is-selected {
  border-color: #52c2fa;
  box-shadow: 0 0 9px rgba(27, 126, 242, 0.3), inset 0 0 12px rgba(59, 93, 212, 0.18);
}
.left-sidebar__algorithm-item.is-selected i { box-shadow: 0 0 10px rgba(27, 126, 242, 0.65); }
.left-sidebar__algorithm-item.is-selected i::after { background: #fff; }

.left-sidebar__progress {
  position: absolute;
  z-index: 3;
  left: 20px;
  top: 733px;
  width: 373px;
  height: 4px;
  background: rgba(208, 222, 238, 0.1);
}
.left-sidebar__progress-fill {
  height: 100%;
  background: linear-gradient(90deg, rgba(36, 145, 200, 0.4), #5ce4ff);
  box-shadow: 0 0 8px rgba(92, 228, 255, 0.55);
  transition: width 0.35s ease;
}
.left-sidebar__progress-knob {
  position: absolute;
  top: 50%;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #fff;
  transform: translate(-50%, -50%);
  box-shadow: 0 0 8px #5ce4ff;
}

.left-sidebar__controls {
  position: absolute;
  z-index: 4;
  left: 21px;
  top: 760px;
  width: 382px;
  height: 40px;
  display: grid;
  grid-template-columns: 116px 116px 116px;
  gap: 16px;
}
.left-sidebar__controls button {
  border: 0;
  clip-path: polygon(3% 14%, 6% 0, 100% 0, 100% 100%, 0 100%, 0 16%);
  background: linear-gradient(180deg, #2e519e, #3c8de7);
  color: #e0f0ff;
  font: 700 15px/1 'PingFang SC', 'Microsoft YaHei', sans-serif;
  cursor: pointer;
  transition: filter 0.2s ease, transform 0.2s ease;
}
.left-sidebar__controls button:hover:not(:disabled),
.left-sidebar__controls button:focus-visible { filter: brightness(1.15) drop-shadow(0 0 6px #52c2fa); outline: none; transform: translateY(-1px); }
.left-sidebar__controls button:disabled { opacity: 0.42; cursor: not-allowed; }


/* 第二张参考图 439×870 精确布局覆盖 */
.left-sidebar__status-dot {
  position: absolute; z-index: 8; top: 49px; right: 50px; width: 8px; height: 8px; padding: 0;
  border: 0; border-radius: 50%; background: #ffb458; box-shadow: 0 0 8px #ffb458; cursor: help;
}
.left-sidebar__status-dot.is-feedback { background: #62e9ff; box-shadow: 0 0 8px #21e6ff; }
.left-sidebar__field { width: auto; gap: 5px; }
.left-sidebar__field-label { height: 19px; color: #accde6; font-size: 15px; font-weight: 600; line-height: 19px; }
.left-sidebar__select :deep(.el-select__wrapper) { min-height: 36px; padding: 4px 12px; border-color: rgba(27,126,242,.45); }
.left-sidebar__select :deep(.el-select__selected-item), .left-sidebar__select :deep(.el-select__placeholder) { font-weight: 600; }
.left-sidebar__config-summary {
  left: 32px; top: 252px; width: 330px; height: 51px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 0; padding: 5px 12px; border-color: rgba(98,190,255,.7);
  background: linear-gradient(180deg,rgba(3,38,73,.88),rgba(1,20,46,.82)); color: #edf8ff;
  font-size: 12px; font-weight: 600; line-height: 1.35; white-space: nowrap;
}
.left-sidebar__file-actions {
  position: absolute; z-index: 4; left: 28px; top: 312px; width: 334px; height: 42px;
  display: grid; grid-template-columns: 161px 161px; gap: 12px;
}
.left-sidebar__file-actions input { display: none; }
.left-sidebar__file-actions button, .left-sidebar__controls button {
  border: 1px solid #52c2fa; color: #eefaff; font-family: inherit; font-weight: 700; cursor: pointer;
  clip-path: polygon(6px 0,100% 0,100% 100%,0 100%,0 7px); background: linear-gradient(180deg,#2e519e,#3c8de7);
  box-shadow: inset 0 1px 0 rgba(173,235,255,.55); transition: filter .2s ease,transform .2s ease;
}
.left-sidebar__file-actions button { font-size: 15px; white-space: nowrap; }
.left-sidebar__file-actions button:hover, .left-sidebar__file-actions button:focus-visible,
.left-sidebar__controls button:hover:not(:disabled), .left-sidebar__controls button:focus-visible {
  filter: brightness(1.14) drop-shadow(0 0 5px #52c2fa); outline: none; transform: translateY(-1px);
}
.left-sidebar__algorithm-item {
  left: 35px; width: 328px; height: 34px; padding: 0 42px 0 18px; border-radius: 4px; font-size: 14px; font-weight: 600;
}
.left-sidebar__algorithm-item em { margin-left: auto; color: #80b9d8; font-size: 8px; font-style: normal; letter-spacing: .08em; }
.left-sidebar__algorithm-item i { right: 11px; width: 18px; height: 18px; background: #071828; }
.left-sidebar__algorithm-item i::after { inset: 6px; }
.left-sidebar__progress { left: 25px; top: 680px; width: 263px; height: 3px; }
.left-sidebar__progress-knob { width: 7px; height: 7px; }
.left-sidebar__speed-badge {
  position: absolute; z-index: 12; left: 315px; top: 663px; width: 68px; height: 34px; display: grid; place-items: center;
  padding: 0; border: 1px solid rgba(89,147,255,.7); border-radius: 18px; background: rgba(2,19,42,.9);
  box-shadow: inset 0 0 9px rgba(33,139,255,.14), 0 0 7px rgba(33,139,255,.12);
  color: #fff; font: 600 14px/1 inherit; cursor: pointer;
}
.left-sidebar__speed-badge:hover:not(:disabled), .left-sidebar__speed-badge:focus-visible, .left-sidebar__speed-badge.is-open {
  border-color: #52c2fa; box-shadow: 0 0 9px rgba(33,230,255,.45); outline: none;
}
.left-sidebar__speed-badge:disabled { opacity: .6; cursor: not-allowed; }
.left-sidebar__multiplier { position: absolute; inset: 0; pointer-events: none; }
.left-sidebar__multiplier :deep(.el-tooltip__trigger) { pointer-events: auto; }
:global(.left-sidebar-speed-popper.el-popper) {
  min-width: 76px !important; padding: 5px !important; border: 1px solid rgba(82,194,250,.65) !important;
  border-radius: 8px !important; background: rgba(2,19,42,.98) !important; box-shadow: 0 0 14px rgba(33,139,255,.32) !important;
}
:global(.left-sidebar-speed-popper .el-popper__arrow::before) { border-color: rgba(82,194,250,.65) !important; background: #02132a !important; }
:global(.left-sidebar-speed-popper .el-dropdown-menu) { padding: 0; background: transparent; }
:global(.left-sidebar-speed-popper .el-dropdown-menu__item) {
  justify-content: center; height: 29px; padding: 0 10px; border-radius: 5px; color: #b9d9ec;
  font: 600 13px/1 'PingFang SC','Microsoft YaHei',sans-serif;
}
:global(.left-sidebar-speed-popper .el-dropdown-menu__item:hover),
:global(.left-sidebar-speed-popper .el-dropdown-menu__item:focus),
:global(.left-sidebar-speed-popper .el-dropdown-menu__item.is-selected) {
  background: rgba(33,139,255,.24); color: #fff; text-shadow: 0 0 7px #21e6ff;
}
.left-sidebar__mock-note { position: absolute; z-index: 4; left: 35px; top: 647px; color: #8eb5cf; font-size: 9px; }
.left-sidebar__controls { left: 21px; top: 703px; width: 382px; height: 40px; }
.left-sidebar__controls button { border-width: 1px; display: grid; place-items: center; font-size: 18px; font-weight: 800; line-height: 1; letter-spacing: .02em; text-shadow: 0 1px 3px rgba(0,25,64,.65), 0 0 6px rgba(92,228,255,.2); white-space: nowrap; }
.left-sidebar__runtime {
  position: absolute; z-index: 4; left: 28px; top: 758px; width: 356px; height: 82px;
  padding-top: 8px; border-top: 1px solid rgba(82,194,250,.28); color: #b9d9ec;
}
.left-sidebar__runtime-head { height: 22px; display: flex; align-items: center; gap: 10px; font-size: 10px; }
.left-sidebar__runtime-head strong { display: flex; align-items: center; gap: 6px; color: #d8eaff; font-size: 11px; }
.left-sidebar__runtime-head strong i { width: 6px; height: 6px; border-radius: 50%; background: #8da3b5; box-shadow: 0 0 6px currentColor; }
.left-sidebar__runtime-head span { color: #7fa9c4; }
.left-sidebar__runtime-head em { min-width: 0; margin-left: auto; overflow: hidden; color: #8fc6e5; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
.left-sidebar__runtime.is-running .left-sidebar__runtime-head strong { color: #58f0ae; }
.left-sidebar__runtime.is-running .left-sidebar__runtime-head strong i { background: #3ce69a; }
.left-sidebar__runtime.is-starting .left-sidebar__runtime-head strong,
.left-sidebar__runtime.is-stopping .left-sidebar__runtime-head strong { color: #ffe47a; }
.left-sidebar__runtime.is-starting .left-sidebar__runtime-head strong i,
.left-sidebar__runtime.is-stopping .left-sidebar__runtime-head strong i { background: #e8b94c; }
.left-sidebar__runtime.is-failed .left-sidebar__runtime-head strong { color: #ff9d9d; }
.left-sidebar__runtime.is-failed .left-sidebar__runtime-head strong i { background: #ff6b6b; }
.left-sidebar__runtime dl { height: 45px; display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 7px; margin: 3px 0 0; }
.left-sidebar__runtime dl div { min-width: 0; }
.left-sidebar__runtime dt { color: #668fa9; font-size: 9px; white-space: nowrap; }
.left-sidebar__runtime dd { margin: 3px 0 0; overflow: hidden; color: #eefaff; font-size: 11px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }

@media (prefers-reduced-motion: reduce) {
  .left-sidebar__algorithm-item,
  .left-sidebar__progress-fill,
  .left-sidebar__file-actions button,
  .left-sidebar__controls button { transition: none; }
}
</style>
