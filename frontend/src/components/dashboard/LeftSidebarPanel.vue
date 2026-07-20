<script setup lang="ts">
import { computed, ref } from 'vue'
import { DISTURBANCE_CHOICE_OPTIONS, FLOW_SCALE_SELECT_OPTIONS, SCENARIO_MODE_OPTIONS, TRAFFIC_FLOW_MODE_OPTIONS } from '../../constants/scenarioOptions'
import { DASHBOARD_CONTROL_MODES } from '../../constants/simulationOptions'
import { useCompactScenarioConfig } from '../../composables/useCompactScenarioConfig'
import { useCatalog } from '../../composables/useCatalog'
import LeftSidebarFrameSvg from './LeftSidebarFrameSvg.vue'
import LeftSidebarBottomChrome from './LeftSidebarBottomChrome.vue'
import LeftSidebarSectionHeader from './LeftSidebarSectionHeader.vue'
import { LEFT_SIDEBAR_DESIGN_HEIGHT, LEFT_SIDEBAR_DESIGN_WIDTH, LEFT_SIDEBAR_REFERENCE_LAYOUT } from '../../constants/leftSidebarLayout'
import type { SimulationSnapshot, SimulationState, StartSimulationRequest } from '../../types/simulation'

const props = defineProps<{ sessionId: string; state: SimulationState | null; snapshot: SimulationSnapshot | null; starting: boolean; controlling: boolean; startError: string | null; controlError: string | null; healthReady: boolean; healthLabel: string }>()
const emit = defineEmits<{ start: [payload: StartSimulationRequest]; stop: [] }>()

const { intersection, periods, error: catalogError } = useCatalog()
const { config, configNote, availableTimeOptions, buildPayload, applyImportedConfig, buildExport } = useCompactScenarioConfig(intersection, periods)
const fileInput = ref<HTMLInputElement | null>(null)
const feedback = ref<string | null>(null)
const multiplierOpen = ref(false)
const fields = computed(() => [
  { key: 'scenario', label: '场景模式', options: SCENARIO_MODE_OPTIONS },
  { key: 'disturbance', label: '扰动事件', options: DISTURBANCE_CHOICE_OPTIONS },
  { key: 'flow', label: '交通流模式', options: TRAFFIC_FLOW_MODE_OPTIONS },
  { key: 'time', label: '仿真时间', options: availableTimeOptions.value },
])
const isRunning = computed(() => props.state === 'RUNNING' || props.state === 'STARTING' || props.state === 'STOPPING')
const canStop = computed(() => isRunning.value)
const canStart = computed(() => props.healthReady && !props.starting && !isRunning.value)
const progressPercent = computed(() => typeof props.snapshot?.progress === 'number' ? Math.min(100, Math.max(0, props.snapshot.progress * 100)) : 0)
const statusMessage = computed(() => feedback.value || props.startError || props.controlError || catalogError.value || (!props.healthReady ? props.healthLabel : ''))
const selectedAlgorithm = computed(() => DASHBOARD_CONTROL_MODES.find((item) => item.value === config.value.control_mode))

function fieldModel(key: string): 'scenario_mode' | 'disturbance' | 'flow_mode' | 'time_preset' {
  if (key === 'scenario') return 'scenario_mode'
  if (key === 'disturbance') return 'disturbance'
  if (key === 'flow') return 'flow_mode'
  return 'time_preset'
}
function openFilePicker() { fileInput.value?.click() }
async function importConfig(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try { applyImportedConfig(JSON.parse(await file.text())); feedback.value = '配置参数已载入' }
  catch (error) { feedback.value = error instanceof Error ? error.message : '配置导入失败' }
  finally { input.value = '' }
}
function exportConfig() {
  const blob = new Blob([JSON.stringify(buildExport(), null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `xiongan-simulation-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.json`
  link.click()
  URL.revokeObjectURL(url)
  feedback.value = '当前仿真场景已导出'
}
function handleStart() { multiplierOpen.value = false; emit('start', buildPayload()) }
function selectMultiplier(value: number) {
  if (isRunning.value) return
  config.value.flow_scale = value
  multiplierOpen.value = false
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
        <el-select v-model="config[fieldModel(field.key)]" class="left-sidebar__select" popper-class="left-sidebar-select-popper">
          <el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </label>

      <div class="left-sidebar__config-summary" :title="configNote">
        <span v-for="line in configNote.split('\n')" :key="line">{{ line }}</span>
      </div>

      <div class="left-sidebar__file-actions">
        <input ref="fileInput" type="file" accept="application/json,.json" @change="importConfig" />
        <button type="button" @click="openFilePicker">上传配置参数</button>
        <button type="button" @click="exportConfig">导出当前仿真场景</button>
      </div>

      <LeftSidebarSectionHeader title="管控算法选择" variant="algorithm" />
      <div class="left-sidebar__algorithm-list" role="radiogroup" aria-label="管控算法选择">
        <label
          v-for="(option, index) in DASHBOARD_CONTROL_MODES"
          :key="option.value"
          class="left-sidebar__algorithm-item"
          :class="{ 'is-selected': config.control_mode === option.value }"
          :style="{ top: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.algorithmItems.top + index * (LEFT_SIDEBAR_REFERENCE_LAYOUT.algorithmItems.height + LEFT_SIDEBAR_REFERENCE_LAYOUT.algorithmItems.gap)}px` }"
          :title="option.backendSupported ? '真实后端算法' : '模拟展示；启动时由固定配时后端承载'"
        >
          <span>{{ option.label }}</span><em v-if="!option.backendSupported">MOCK</em>
          <input v-model="config.control_mode" type="radio" name="sidebar-algorithm" :value="option.value" /><i aria-hidden="true" />
        </label>
      </div>

      <LeftSidebarBottomChrome />
      <div class="left-sidebar__progress" role="progressbar" :aria-valuenow="Math.round(progressPercent)" aria-valuemin="0" aria-valuemax="100">
        <div class="left-sidebar__progress-fill" :style="{ width: `${progressPercent}%` }" />
        <span class="left-sidebar__progress-knob" :style="{ left: `${progressPercent}%` }" />
      </div>
      <div class="left-sidebar__multiplier" @keydown="handleMultiplierKeydown">
        <button
          type="button"
          class="left-sidebar__speed-badge"
          :class="{ 'is-open': multiplierOpen }"
          :disabled="isRunning"
          :aria-expanded="multiplierOpen"
          aria-haspopup="listbox"
          :title="isRunning ? '仿真运行期间不能修改流量倍率' : '选择仿真流量倍率'"
          @click="multiplierOpen = !multiplierOpen"
        >×{{ config.flow_scale.toFixed(1) }}</button>
        <div v-if="multiplierOpen" class="left-sidebar__speed-menu" role="listbox" aria-label="仿真流量倍率">
          <button
            v-for="option in FLOW_SCALE_SELECT_OPTIONS"
            :key="option.value"
            type="button"
            role="option"
            :aria-selected="config.flow_scale === option.value"
            :class="{ 'is-selected': config.flow_scale === option.value }"
            @click="selectMultiplier(option.value)"
          >×{{ option.value.toFixed(1) }}</button>
        </div>
      </div>
      <div v-if="selectedAlgorithm && !selectedAlgorithm.backendSupported" class="left-sidebar__mock-note">当前算法为模拟展示，后端使用固定配时</div>

      <div class="left-sidebar__controls">
        <button type="button" :disabled="!canStart" :title="canStart ? '开始仿真' : healthLabel" @click="handleStart">{{ starting ? '启动中…' : '开始仿真' }}</button>
        <button type="button" disabled title="真实后端暂不支持暂停">暂停仿真</button>
        <button type="button" :disabled="!sessionId || !canStop || controlling" @click="emit('stop')">结束仿真</button>
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
  --ls-scale: min(1, calc(100cqw / 439), calc(100cqh / 870));
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
.left-sidebar__speed-menu {
  position: absolute; z-index: 11; left: 315px; top: 531px; width: 68px; padding: 5px;
  border: 1px solid rgba(82,194,250,.65); border-radius: 8px; background: rgba(2,19,42,.97);
  box-shadow: 0 0 14px rgba(33,139,255,.32);
}
.left-sidebar__speed-menu button {
  width: 100%; height: 29px; border: 0; border-radius: 5px; background: transparent; color: #b9d9ec;
  font: 600 13px/1 inherit; cursor: pointer;
}
.left-sidebar__speed-menu button:hover, .left-sidebar__speed-menu button:focus-visible, .left-sidebar__speed-menu button.is-selected {
  background: rgba(33,139,255,.24); color: #fff; outline: none; text-shadow: 0 0 7px #21e6ff;
}
.left-sidebar__mock-note { position: absolute; z-index: 4; left: 35px; top: 647px; color: #8eb5cf; font-size: 9px; }
.left-sidebar__controls { left: 21px; top: 703px; width: 382px; height: 40px; }
.left-sidebar__controls button { border-width: 1px; display: grid; place-items: center; font-size: 18px; font-weight: 800; line-height: 1; letter-spacing: .02em; text-shadow: 0 1px 3px rgba(0,25,64,.65), 0 0 6px rgba(92,228,255,.2); white-space: nowrap; }

@media (prefers-reduced-motion: reduce) {
  .left-sidebar__algorithm-item,
  .left-sidebar__progress-fill,
  .left-sidebar__file-actions button,
  .left-sidebar__controls button { transition: none; }
}
</style>
