<script setup lang="ts">
import { computed } from 'vue'
import {
  DISTURBANCE_CHOICE_OPTIONS,
  DURATION_OPTIONS,
  FLOW_SCALE_SELECT_OPTIONS,
  OD_PRESET_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
} from '../../constants/scenarioOptions'
import { resolveControlModeLabel } from '../../constants/simulationOptions'
import { useCompactScenarioConfig } from '../../composables/useCompactScenarioConfig'
import { useCatalog } from '../../composables/useCatalog'
import LeftSidebarFrameSvg from './LeftSidebarFrameSvg.vue'
import LeftSidebarBottomChrome from './LeftSidebarBottomChrome.vue'
import LeftSidebarSectionHeader from './LeftSidebarSectionHeader.vue'
import {
  LEFT_SIDEBAR_CLIP_INSET_BOTTOM,
  LEFT_SIDEBAR_CLIP_INSET_LEFT,
  LEFT_SIDEBAR_CLIP_INSET_RIGHT,
  LEFT_SIDEBAR_CLIP_INSET_TOP,
  LEFT_SIDEBAR_CONTENT_OFFSET,
  LEFT_SIDEBAR_CONTENT_SCALE,
  LEFT_SIDEBAR_DESIGN_HEIGHT,
  LEFT_SIDEBAR_DESIGN_WIDTH,
} from '../../constants/leftSidebarLayout'
import type {
  SimulationSnapshot,
  SimulationState,
  StartSimulationRequest,
} from '../../types/simulation'

const props = defineProps<{
  sessionId: string
  state: SimulationState | null
  snapshot: SimulationSnapshot | null
  starting: boolean
  controlling: boolean
  startError: string | null
  controlError: string | null
  healthReady: boolean
  healthLabel: string
}>()

const emit = defineEmits<{
  start: [payload: StartSimulationRequest]
  stop: []
}>()

const {
  intersection,
  periods,
  controlModes,
  error: catalogError,
} = useCatalog()

const { config, configNote, buildPayload } = useCompactScenarioConfig(intersection, periods)

const controlModeOptions = computed(() =>
  controlModes.value.map((mode) => ({ value: mode, label: resolveControlModeLabel(mode) })),
)

const scenarioFields = computed(() => [
  { key: 'flow_mode', label: '交通流模式', type: 'flow_mode' as const },
  { key: 'od_preset_id', label: '起始点OD', type: 'od' as const },
  { key: 'disturbance', label: '扰动事件', type: 'disturbance' as const },
  { key: 'flow_scale', label: '交通流倍率', type: 'flow_scale' as const },
  { key: 'duration', label: '仿真时长', type: 'duration' as const },
])

const isRunning = computed(
  () => props.state === 'RUNNING' || props.state === 'STARTING' || props.state === 'STOPPING',
)
const canStop = computed(() => isRunning.value)
const canStart = computed(
  () => props.healthReady && !props.starting && !isRunning.value,
)

const progressPercent = computed(() => {
  const progress = props.snapshot?.progress
  if (typeof progress === 'number') {
    return Math.min(100, progress * 100)
  }
  return 0
})

function handleStop() {
  emit('stop')
}

function handleStart() {
  emit('start', buildPayload())
}
</script>

<template>
  <section class="left-sidebar" aria-label="左侧数据面板">
    <div
      class="left-sidebar__scaler"
      :style="{
        width: `${LEFT_SIDEBAR_DESIGN_WIDTH}px`,
        height: `${LEFT_SIDEBAR_DESIGN_HEIGHT}px`,
        '--dashboard-left-sidebar-design-width': `${LEFT_SIDEBAR_DESIGN_WIDTH}px`,
      }"
    >
      <div
        class="left-sidebar__canvas"
        :style="{
          width: `${LEFT_SIDEBAR_DESIGN_WIDTH}px`,
          height: `${LEFT_SIDEBAR_DESIGN_HEIGHT}px`,
          '--ls-content-scale': LEFT_SIDEBAR_CONTENT_SCALE,
        }"
      >
      <LeftSidebarFrameSvg class="left-sidebar__frame" />

      <div
        class="left-sidebar__clip"
        :style="{
          top: `${LEFT_SIDEBAR_CLIP_INSET_TOP}px`,
          left: `${LEFT_SIDEBAR_CLIP_INSET_LEFT}px`,
          right: `${LEFT_SIDEBAR_CLIP_INSET_RIGHT}px`,
          bottom: `${LEFT_SIDEBAR_CLIP_INSET_BOTTOM}px`,
        }"
      >
        <div
          class="left-sidebar__content"
          :style="{
            left: `${LEFT_SIDEBAR_CONTENT_OFFSET.x}px`,
            top: `${LEFT_SIDEBAR_CONTENT_OFFSET.y}px`,
          }"
        >
      <LeftSidebarSectionHeader title="仿真场景配置" variant="scenario" />

      <!-- 表单区 -->
      <div class="left-sidebar__fields">
        <div
          v-if="catalogError || startError || controlError || !healthReady"
          class="left-sidebar__alerts"
        >
          <el-alert
            v-if="!healthReady"
            :title="healthLabel"
            type="warning"
            show-icon
            :closable="false"
          />
          <el-alert
            v-if="catalogError"
            :title="catalogError"
            type="warning"
            show-icon
            :closable="false"
          />
          <el-alert
            v-if="startError"
            :title="startError"
            type="error"
            show-icon
            :closable="false"
          />
          <el-alert
            v-if="controlError"
            :title="controlError"
            type="warning"
            show-icon
            :closable="false"
          />
        </div>

        <label
          v-for="(field, index) in scenarioFields"
          :key="field.key"
          class="left-sidebar__field"
          :class="`left-sidebar__field--${index + 1}`"
        >
          <span class="left-sidebar__field-label">{{ field.label }}</span>

          <el-select
            v-if="field.type === 'flow_mode'"
            v-model="config.flow_mode"
            class="left-sidebar__select"
            popper-class="left-sidebar-select-popper"
          >
            <el-option
              v-for="option in TRAFFIC_FLOW_MODE_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>

          <el-select
            v-else-if="field.type === 'od'"
            v-model="config.od_preset_id"
            class="left-sidebar__select"
            popper-class="left-sidebar-select-popper"
          >
            <el-option
              v-for="option in OD_PRESET_OPTIONS"
              :key="option.id"
              :label="option.label"
              :value="option.id"
            />
          </el-select>

          <el-select
            v-else-if="field.type === 'disturbance'"
            v-model="config.disturbance"
            class="left-sidebar__select"
            popper-class="left-sidebar-select-popper"
          >
            <el-option
              v-for="option in DISTURBANCE_CHOICE_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>

          <el-select
            v-else-if="field.type === 'flow_scale'"
            v-model="config.flow_scale"
            class="left-sidebar__select"
            popper-class="left-sidebar-select-popper"
          >
            <el-option
              v-for="option in FLOW_SCALE_SELECT_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>

          <el-select
            v-else-if="field.type === 'duration'"
            v-model="config.duration"
            class="left-sidebar__select"
            popper-class="left-sidebar-select-popper"
          >
            <el-option
              v-for="option in DURATION_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </label>
      </div>

      <!-- 当前配置摘要 -->
      <div class="left-sidebar__config-summary">
        <p class="left-sidebar__config-summary-text">{{ configNote }}</p>
      </div>

      <LeftSidebarSectionHeader title="管控算法选择" variant="algorithm" />

      <!-- 控制模式单选（来自 catalog control_modes） -->
      <div class="left-sidebar__algorithm-list" role="radiogroup" aria-label="管控算法选择">
        <label
          v-for="(option, index) in controlModeOptions"
          :key="option.value"
          class="left-sidebar__algorithm-item"
          :class="[`left-sidebar__algorithm-item--${index + 1}`, { 'is-selected': config.control_mode === option.value }]"
        >
          <span class="left-sidebar__algorithm-label">{{ option.label }}</span>
          <input
            v-model="config.control_mode"
            class="left-sidebar__algorithm-input"
            type="radio"
            name="sidebar-algorithm"
            :value="option.value"
          />
          <span class="left-sidebar__algorithm-radio" aria-hidden="true" />
        </label>
      </div>

      <LeftSidebarBottomChrome :progress-percent="progressPercent" />

      <!-- 仿真进度条 -->
      <div
        class="left-sidebar__progress"
        role="progressbar"
        :aria-valuenow="Math.round(progressPercent)"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div
          class="left-sidebar__progress-fill"
          :style="{ width: `${progressPercent}%` }"
        />
        <span
          v-if="progressPercent > 0"
          class="left-sidebar__progress-knob"
          :style="{ left: `${progressPercent}%` }"
          aria-hidden="true"
        />
      </div>

      <!-- 底部三按钮 -->
      <div class="left-sidebar__controls">
        <button
          type="button"
          class="left-sidebar__control-btn left-sidebar__control-btn--1"
          :disabled="!canStart"
          @click="handleStart"
        >
          <span class="left-sidebar__control-btn-bg" aria-hidden="true" />
          <span class="left-sidebar__control-btn-border" aria-hidden="true" />
          <span class="left-sidebar__control-btn-text">
            {{ starting ? '启动中...' : '开始仿真' }}
          </span>
        </button>

        <button
          type="button"
          class="left-sidebar__control-btn left-sidebar__control-btn--2"
          disabled
          title="真实后端暂不支持暂停"
        >
          <span class="left-sidebar__control-btn-bg" aria-hidden="true" />
          <span class="left-sidebar__control-btn-border" aria-hidden="true" />
          <span class="left-sidebar__control-btn-text">暂停仿真</span>
        </button>

        <button
          type="button"
          class="left-sidebar__control-btn left-sidebar__control-btn--3"
          :disabled="!sessionId || !canStop || controlling"
          @click="handleStop"
        >
          <span class="left-sidebar__control-btn-bg" aria-hidden="true" />
          <span class="left-sidebar__control-btn-border" aria-hidden="true" />
          <span class="left-sidebar__control-btn-text">结束仿真</span>
        </button>
      </div>
        </div>
      </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.left-sidebar {
  container-type: size;
  display: flex;
  justify-content: flex-start;
  align-items: flex-start;
  padding-left: 10px;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  pointer-events: auto;
}

.left-sidebar__scaler {
  transform-origin: top left;
  transform: scale(
    min(1, 100cqw / var(--dashboard-left-sidebar-design-width, 560px), 100cqh / 990px)
  );
}

.left-sidebar__canvas {
  --ls-w: 417;
  --ls-h: 870;
  --ls-design-w: 560;
  --ls-design-h: 990;
  --ls-btn-clip: polygon(
    0% 14.75%,
    2.98% 0.82%,
    49.5% 1.64%,
    100% 1.64%,
    100% 27.3%,
    100% 74.34%,
    100% 100%,
    0.4% 100%,
    0% 74.34%
  );
  --ls-btn-fill-clip: polygon(
    100% 0%,
    2.78% 0%,
    0.06% 13.9%,
    0.06% 98.7%,
    100% 98.7%,
    100% 73%,
    100% 49.4%,
    100% 25.8%,
    100% 0%
  );
  position: relative;
  flex-shrink: 0;
  background: transparent;
  overflow: hidden;
  color: var(--ls-text-white);
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.left-sidebar__frame {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}

.left-sidebar__clip {
  position: absolute;
  z-index: 1;
  overflow: hidden;
  pointer-events: none;
}

.left-sidebar__content {
  position: absolute;
  width: 417px;
  height: 870px;
  transform: scale(var(--ls-content-scale));
  transform-origin: top left;
  pointer-events: none;
}

/* ── 表单双列 164×37 ── */
.left-sidebar__fields {
  position: absolute;
  z-index: 2;
  inset: 0;
  pointer-events: none;
}

.left-sidebar__alerts {
  position: absolute;
  top: calc(88 / var(--ls-h) * 100%);
  left: calc(20 / var(--ls-w) * 100%);
  right: calc(20 / var(--ls-w) * 100%);
  pointer-events: auto;
}

.left-sidebar__field {
  position: absolute;
  width: calc(185 / var(--ls-w) * 100%);
  display: flex;
  flex-direction: column;
  gap: 5px;
  pointer-events: auto;
}

.left-sidebar__field--1 { left: calc(12 / var(--ls-w) * 100%); top: calc(100 / var(--ls-h) * 100%); }
.left-sidebar__field--2 { left: calc(220 / var(--ls-w) * 100%); top: calc(100 / var(--ls-h) * 100%); }
.left-sidebar__field--3 { left: calc(12 / var(--ls-w) * 100%); top: calc(190 / var(--ls-h) * 100%); }
.left-sidebar__field--4 { left: calc(220 / var(--ls-w) * 100%); top: calc(190 / var(--ls-h) * 100%); }
.left-sidebar__field--5 { left: calc(12 / var(--ls-w) * 100%); top: calc(279 / var(--ls-h) * 100%); }
.left-sidebar__field--6 { left: calc(220 / var(--ls-w) * 100%); top: calc(279 / var(--ls-h) * 100%); }

.left-sidebar__field-label {
  color: rgba(182, 223, 255, 0.88);
  font-size: 17px;
}

.left-sidebar__select {
  width: 100%;
}

.left-sidebar__select :deep(.el-select__wrapper) {
  min-height: 46px;
  border-radius: 5px;
  background: linear-gradient(90deg, var(--ls-field-gradient-start), var(--ls-field-gradient-end));
  border: 1px solid rgba(27, 126, 242, 0.28);
  box-shadow: inset 0 0 10px rgba(33, 230, 255, 0.04);
}

.left-sidebar__select :deep(.el-select__placeholder),
.left-sidebar__select :deep(.el-select__selected-item) {
  color: var(--ls-text-white);
  font-size: 18px;
}

.left-sidebar__select :deep(.el-select__caret) {
  display: none;
}

.left-sidebar__select :deep(.el-select__suffix)::after {
  content: '';
  display: block;
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 8px solid var(--ls-gold);
}

/* ── 算法列表（红框区域 2） ── */
.left-sidebar__config-summary {
  position: absolute;
  z-index: 2;
  left: calc(12 / var(--ls-w) * 100%);
  top: calc(368 / var(--ls-h) * 100%);
  width: calc(393 / var(--ls-w) * 100%);
  padding: 10px 14px;
  border-radius: 5px;
  border: 1px solid rgba(33, 230, 255, 0.18);
  background: linear-gradient(
    135deg,
    rgba(2, 16, 40, 0.72) 0%,
    rgba(10, 35, 65, 0.58) 100%
  );
  pointer-events: auto;
}

.left-sidebar__config-summary-text {
  margin: 0;
  color: rgba(182, 223, 255, 0.92);
  font-size: 14px;
  line-height: 1.55;
  letter-spacing: 0.2px;
}

.left-sidebar__algorithm-list {
  position: absolute;
  z-index: 2;
  inset: 0;
  pointer-events: none;
}

.left-sidebar__algorithm-item {
  position: absolute;
  left: calc(12 / var(--ls-w) * 100%);
  width: calc(393 / var(--ls-w) * 100%);
  min-height: 46px;
  display: flex;
  align-items: center;
  padding: 0 42px 0 14px;
  border-radius: 5px;
  border: 1px solid rgba(27, 126, 242, 0.32);
  background: linear-gradient(90deg, var(--ls-field-gradient-start), var(--ls-field-gradient-end));
  cursor: pointer;
  pointer-events: auto;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.left-sidebar__algorithm-item--1 { top: calc(503 / var(--ls-h) * 100%); }
.left-sidebar__algorithm-item--2 { top: calc(550 / var(--ls-h) * 100%); }
.left-sidebar__algorithm-item--3 { top: calc(599 / var(--ls-h) * 100%); }
.left-sidebar__algorithm-item--4 { top: calc(648 / var(--ls-h) * 100%); }

.left-sidebar__algorithm-item.is-selected {
  border-color: rgba(82, 194, 250, 0.85);
  box-shadow:
    inset 0 0 14px rgba(59, 93, 212, 0.22),
    0 0 8px rgba(82, 194, 250, 0.15);
}

.left-sidebar__algorithm-label {
  color: var(--ls-text-white);
  font-size: 19px;
  font-weight: 500;
}

.left-sidebar__algorithm-input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.left-sidebar__algorithm-radio {
  position: absolute;
  right: 12px;
  top: 50%;
  width: 22px;
  height: 22px;
  border: 2px solid rgba(27, 126, 242, 0.45);
  border-radius: 50%;
  background: #161616;
  transform: translateY(-50%);
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
}

.left-sidebar__algorithm-radio::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: transparent;
  transform: translate(-50%, -50%);
  transition: background 0.2s ease;
}

.left-sidebar__algorithm-item.is-selected .left-sidebar__algorithm-radio {
  border-color: #52c2fa;
  background: #1b7ef2;
  box-shadow: 0 0 10px rgba(82, 194, 250, 0.55);
}

.left-sidebar__algorithm-item.is-selected .left-sidebar__algorithm-radio::after {
  background: #fff;
}

/* ── 底部仿真进度条 ── */
.left-sidebar__progress {
  position: absolute;
  z-index: 2;
  left: calc(12 / var(--ls-w) * 100%);
  width: calc(393 / var(--ls-w) * 100%);
  top: calc(760 / var(--ls-h) * 100%);
  height: calc(4 / var(--ls-h) * 100%);
  background: rgba(208, 222, 238, 0.1);
  border-radius: 2px;
  overflow: visible;
}

.left-sidebar__progress-fill {
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(
    90deg,
    rgba(36, 145, 200, 0.15) 0%,
    rgba(36, 145, 200, 0.4) 35%,
    #5ce4ff 100%
  );
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
  box-shadow:
    0 0 4px rgba(255, 255, 255, 0.9),
    0 0 10px rgba(92, 228, 255, 0.85);
  transition: left 0.35s ease;
}

/* ── 控制按钮（红框区域 3，SVG 斜切八角形） ── */
.left-sidebar__controls {
  position: absolute;
  z-index: 3;
  left: calc(5 / var(--ls-w) * 100%);
  top: calc(782 / var(--ls-h) * 100%);
  width: calc(407 / var(--ls-w) * 100%);
  height: calc(40 / var(--ls-h) * 100%);
  pointer-events: none;
}

.left-sidebar__control-btn {
  position: absolute;
  top: 0;
  width: calc(127 / 407 * 100%);
  height: 100%;
  border: none;
  background: transparent;
  cursor: pointer;
  pointer-events: auto;
  padding: 0;
}

.left-sidebar__control-btn--1 { left: 0; }
.left-sidebar__control-btn--2 { left: calc(140 / 407 * 100%); }
.left-sidebar__control-btn--3 { left: calc(280 / 407 * 100%); }

.left-sidebar__control-btn-bg {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, #2e519e 0%, #3c8de7 100%);
  clip-path: var(--ls-btn-fill-clip);
}

.left-sidebar__control-btn-border {
  display: none;
}

.left-sidebar__control-btn-text {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--ls-text-white);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
}

.left-sidebar__control-btn:hover:not(:disabled) .left-sidebar__control-btn-bg {
  filter: brightness(1.12);
}

.left-sidebar__control-btn:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

@media (max-width: 1320px) {
  .left-sidebar__canvas {
    width: min(560px, 100%);
    height: auto;
    aspect-ratio: 560 / 990;
  }
}
</style>
