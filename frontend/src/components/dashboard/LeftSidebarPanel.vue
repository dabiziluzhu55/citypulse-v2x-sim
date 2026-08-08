<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  DISTURBANCE_EVENT_OPTIONS,
  SCENARIO_MODE_OPTIONS,
  SIMULATION_PERIOD_RANGES,
  TRAFFIC_FLOW_MODE_OPTIONS,
  clockTimeToMinutes,
  defaultSimulationTimeWindow,
  maximumSimulationEndTime,
  minutesToClockTime,
  type DisturbancePresetId,
} from '../../constants/scenarioOptions'
import {
  DEFAULT_MAJOR_EVENT_VEHICLE_COUNT,
  MAX_MAJOR_EVENT_VEHICLE_COUNT,
  MIN_MAJOR_EVENT_VEHICLE_COUNT,
} from '../../utils/scenarioConfigMigration'
import {
  DASHBOARD_CONTROL_MODES,
  controlModePeriodCompatibility,
  resolveControlModeLabel,
} from '../../constants/simulationOptions'
import { exportScenarioArchive } from '../../api/scenario'
import { simulationApiErrorMessage } from '../../api/client'
import {
  useCompactScenarioConfig,
  type CompactScenarioConfig,
  type CompactDisturbanceEvent,
} from '../../composables/useCompactScenarioConfig'
import {
  comparisonContractDifferences,
  comparisonChangeRequiresConfirmation,
  createScenarioFingerprint,
} from '../../composables/useEvaluationComparison'
import { useCatalog } from '../../composables/useCatalog'
import { useActiveIntersectionScene } from '../../composables/useActiveIntersectionScene'
import {
  findCatalogScenarioPreset,
  missingPresetIntersectionIds,
} from '../../composables/catalogCapabilities'
import LeftSidebarFrameSvg from './LeftSidebarFrameSvg.vue'
import LeftSidebarBottomChrome from './LeftSidebarBottomChrome.vue'
import LeftSidebarSectionHeader from './LeftSidebarSectionHeader.vue'
import HourMinuteStepper from './HourMinuteStepper.vue'
import { LEFT_SIDEBAR_DESIGN_HEIGHT, LEFT_SIDEBAR_DESIGN_WIDTH, LEFT_SIDEBAR_REFERENCE_LAYOUT } from '../../constants/leftSidebarLayout'
import type { SimulationSnapshot, SimulationState, StartSimulationRequest } from '../../types/simulation'
import { formatIntersectionLabel } from '../../utils/intersectionLabels'
import { validateScenarioArchive } from '../../utils/scenarioArchiveValidation'
import {
  canPauseSimulation,
  canResumeSimulation,
  isActiveSimulationState,
  simulationStateLabel,
} from '../../utils/simulationSessionState'
import {
  formatMissingIntersectionMessage,
  reconcileEventsForScenario,
  scenarioPresetIntersectionIds,
} from '../../utils/scenarioPresetRules'

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
  achievedPlaybackSpeed: number | null
  displayedOfficialTime: string
  activeComparisonFingerprint: string
  hasActiveComparisonData: boolean
  healthReady: boolean
  healthLabel: string
}>()
const emit = defineEmits<{
  start: [payload: StartSimulationRequest]
  pause: []
  resume: []
  stop: []
  playbackSpeed: [value: number]
  dismissStatusError: []
  configChangeRequested: [request: {
    fingerprint: string
    differences: string[]
    apply: () => void
  }]
}>()

const { activeIntersectionId, selectIntersection } = useActiveIntersectionScene()
const {
  catalog,
  intersection,
  periods,
  controlModes,
  scenarioPresets,
  playbackSpeeds,
  eventTypes,
  supportedIntersectionIds,
  loading: catalogLoading,
  error: catalogError,
  isIntersectionSupported,
} = useCatalog(activeIntersectionId)
const {
  config,
  configNote,
  buildPayload,
  buildPayloadFor,
  buildExport,
} = useCompactScenarioConfig(
  intersection,
  periods,
  scenarioPresets,
  playbackSpeeds,
  supportedIntersectionIds,
  controlModes,
)
const feedback = ref<string | null>(null)
const multiplierOpen = ref(false)
const exporting = ref(false)
const disturbanceModalOpen = ref(false)
const runtimeErrorOpen = ref(false)
const editingDisturbanceId = ref<string | null>(null)
const disturbanceFormError = ref('')
const disturbanceDraft = ref<{
  presetId: DisturbancePresetId
  intersectionIds: string[]
  startTime: string
  endTime: string
  vehicleCount: number
}>({
  presetId: DISTURBANCE_EVENT_OPTIONS[0].value,
  intersectionIds: [],
  startTime: SIMULATION_PERIOD_RANGES.morning_peak.start,
  endTime: defaultSimulationTimeWindow('morning_peak').end,
  vehicleCount: DEFAULT_MAJOR_EVENT_VEHICLE_COUNT,
})
const scenarioOptions = computed(() => SCENARIO_MODE_OPTIONS.map((item) => ({
  label: item.label,
  value: item.value,
})))
const disturbanceIntersectionOptions = computed(() => {
  const candidateIds = scenarioPresetIntersectionIds(
    config.value.scenario_preset_id,
    scenarioPresets.value,
  )
  return candidateIds
    .map((id) => ({ label: formatIntersectionLabel(id), value: id }))
})
const selectedDisturbanceIntersectionCount = computed(() => disturbanceDraft.value.intersectionIds.length)
const selectedDisturbanceOption = computed(() => DISTURBANCE_EVENT_OPTIONS.find(
  (item) => item.value === disturbanceDraft.value.presetId,
))
const isMajorDisturbance = computed(() => (
  selectedDisturbanceOption.value?.eventType === 'major_event_opening'
  || selectedDisturbanceOption.value?.eventType === 'major_event_closing'
))
const availableDisturbanceEventOptions = computed(() => DISTURBANCE_EVENT_OPTIONS.map((option) => ({
  ...option,
  disabled: !eventTypes.value.includes(option.eventType),
})))
const selectedPeriod = computed(() => (
  config.value.flow_mode === 'flat' ? 'off_peak' : config.value.flow_mode
))
const controlModeOptions = computed(() => DASHBOARD_CONTROL_MODES.map((option) => {
  const compatibility = controlModePeriodCompatibility(option.value, selectedPeriod.value)
  return {
    ...option,
    disabled: !controlModes.value.includes(option.value) || !compatibility.compatible,
  }
}))
function controlModeOptionTitle(option: { value: string; disabled: boolean }): string {
  if (!controlModes.value.includes(option.value)) return '当前后端未提供该算法'
  const compatibility = controlModePeriodCompatibility(option.value, selectedPeriod.value)
  if (!compatibility.compatible) return compatibility.reason
  return '真实后端算法'
}
const controlModeUnavailableMessage = computed(() => {
  if (catalogLoading.value) return ''
  if (!controlModes.value.includes(config.value.control_mode)) {
    return `后端未提供当前管控算法：${config.value.control_mode}`
  }
  const compatibility = controlModePeriodCompatibility(
    config.value.control_mode,
    selectedPeriod.value,
  )
  if (!compatibility.compatible) return compatibility.reason
  return ''
})
const simulationStartMinimum = computed(() => SIMULATION_PERIOD_RANGES[config.value.flow_mode].start)
const simulationStartMaximum = computed(() => minutesToClockTime(
  clockTimeToMinutes(SIMULATION_PERIOD_RANGES[config.value.flow_mode].end) - 1,
))
const simulationEndMinimum = computed(() => minutesToClockTime(
  clockTimeToMinutes(config.value.simulation_start_time) + 1,
))
const simulationEndMaximum = computed(() => maximumSimulationEndTime(
  config.value.flow_mode,
  config.value.simulation_start_time,
))
const disturbanceStartMinimum = computed(() => config.value.simulation_start_time)
const disturbanceStartMaximum = computed(() => {
  const outerStart = clockTimeToMinutes(config.value.simulation_start_time)
  const outerEnd = clockTimeToMinutes(config.value.simulation_end_time)
  const eventEnd = clockTimeToMinutes(disturbanceDraft.value.endTime)
  return minutesToClockTime(Math.max(outerStart, Math.min(outerEnd - 1, eventEnd - 1)))
})
const disturbanceEndMinimum = computed(() => {
  const outerEnd = clockTimeToMinutes(config.value.simulation_end_time)
  const eventStart = clockTimeToMinutes(disturbanceDraft.value.startTime)
  return minutesToClockTime(Math.min(outerEnd, eventStart + 1))
})
const disturbanceEndMaximum = computed(() => config.value.simulation_end_time)
const fields = computed(() => [
  { key: 'scenario', label: '场景模式', options: scenarioOptions.value },
  { key: 'flow', label: '交通流模式', options: TRAFFIC_FLOW_MODE_OPTIONS },
])
const isSessionActive = computed(() => props.starting || (
  !!props.sessionId && (!props.state || isActiveSimulationState(props.state))
))
const canStop = computed(() => isSessionActive.value)
const canPause = computed(() => canPauseSimulation(props.state) && !props.controlling)
const canResume = computed(() => canResumeSimulation(props.state) && !props.controlling)
const selectedPreset = computed(() => findCatalogScenarioPreset(catalog.value, config.value.scenario_preset_id))
const presetMissingIds = computed(() => missingPresetIntersectionIds(catalog.value, config.value.scenario_preset_id))
const presetUnavailableMessage = computed(() => {
  if (catalogLoading.value) return ''
  if (!selectedPreset.value) return '后端未提供当前场景预设'
  if (presetMissingIds.value.length > 0) {
    return formatMissingIntersectionMessage(presetMissingIds.value)
  }
  if (!scenarioPresetIntersectionIds(config.value.scenario_preset_id).includes(activeIntersectionId.value)) {
    return `${activeIntersectionId.value} 不属于当前仿真场景，请先切换场景内路口`
  }
  return ''
})
const canStart = computed(() => (
  props.healthReady
  && !catalogLoading.value
  && isIntersectionSupported.value
  && !presetUnavailableMessage.value
  && !controlModeUnavailableMessage.value
  && !props.starting
  && !isSessionActive.value
))
const progressPercent = computed(() => typeof props.snapshot?.progress === 'number' ? Math.min(100, Math.max(0, props.snapshot.progress * 100)) : 0)
const unsupportedMessage = computed(() => (
  !catalogLoading.value && !isIntersectionSupported.value
    ? `${activeIntersectionId.value} 当前仅支持高精度查看，尚未接入真实仿真路网`
    : ''
))
const statusMessage = computed(() => (props.state === 'QUEUED' ? '排队中，等待仿真资源' : '')
  || feedback.value
  || props.startError
  || props.controlError
  || props.statusError
  || catalogError.value
  || presetUnavailableMessage.value
  || unsupportedMessage.value
  || controlModeUnavailableMessage.value
  || (!props.healthReady ? props.healthLabel : ''))
const playbackSpeedOptions = computed(() => playbackSpeeds.value.map((value) => ({ label: `${value}x`, value })))

function dismissStatusMessage(): void {
  feedback.value = null
  if (props.statusError) emit('dismissStatusError')
}
const stateLabel = computed(() => simulationStateLabel(props.state)
  ?? (props.healthReady ? 'READY' : 'OFFLINE'))
const officialTimeLabel = computed(() => {
  const value = props.displayedOfficialTime || props.snapshot?.official_time
  if (!value) return '--:--:--'
  const time = value.includes('T') ? value.split('T')[1] : value
  return time.slice(0, 8)
})
const achievedPlaybackLabel = computed(() => (
  props.achievedPlaybackSpeed == null ? '--' : `${props.achievedPlaybackSpeed.toFixed(2)}×`
))
const playbackBusy = computed(() => (
  props.state === 'RUNNING'
  && props.achievedPlaybackSpeed != null
  && props.achievedPlaybackSpeed < props.activePlaybackSpeed * 0.75
))
const activeVehicleLabel = computed(() => props.snapshot?.metrics.active_vehicles ?? 0)
const algorithmLabel = computed(() => resolveControlModeLabel(
  props.activeControlMode || config.value.control_mode,
))
const runtimeFailureStage = computed(() => (
  (props.snapshot?.sequence ?? 0) <= 0 ? '算法初始化' : '仿真运行'
))
const runtimeRawError = computed(() => (
  props.snapshot?.error?.trim()
  || props.statusError
  || props.startError
  || props.controlError
  || '后端未提供原始错误详情'
))
const playbackSpeedTitle = computed(() => `播放倍率：${config.value.playback_speed}×`)
const startTitle = computed(() => {
  if (catalogLoading.value) return '正在读取真实仿真路口目录'
  if (presetUnavailableMessage.value) return presetUnavailableMessage.value
  if (unsupportedMessage.value) return unsupportedMessage.value
  if (controlModeUnavailableMessage.value) return controlModeUnavailableMessage.value
  if (!props.healthReady) return props.healthLabel
  if (isSessionActive.value) return '当前仿真尚未结束'
  return '开始仿真'
})

watch(
  () => props.activePlaybackSpeed,
  (value) => {
    if (isSessionActive.value && playbackSpeeds.value.includes(value)) config.value.playback_speed = value
  },
  { immediate: true },
)
watch(
  () => props.state,
  (value) => {
    if (value !== 'FAILED') runtimeErrorOpen.value = false
  },
)

watch(
  [() => props.activeControlMode, controlModeOptions, isSessionActive],
  ([value, options, active]) => {
    if (
      active
      && value
      && options.some((option) => option.value === value && !option.disabled)
    ) config.value.control_mode = value
  },
  { immediate: true },
)

watch(
  () => config.value.scenario_preset_id,
  (presetId) => {
    const intersectionIds = scenarioPresetIntersectionIds(presetId, scenarioPresets.value)
    if (intersectionIds.length > 0 && !intersectionIds.includes(activeIntersectionId.value)) {
      selectIntersection(intersectionIds[0])
    }
  },
)

function fieldModel(key: string): 'scenario_preset_id' | 'flow_mode' {
  if (key === 'scenario') return 'scenario_preset_id'
  return 'flow_mode'
}

function applyConfiguration(next: CompactScenarioConfig, onApplied?: () => void): void {
  config.value = { ...next }
  onApplied?.()
}

function requestConfiguration(next: CompactScenarioConfig, onApplied?: () => void): void {
  let fingerprint: string
  try {
    fingerprint = createScenarioFingerprint(
      buildPayloadFor(next),
      scenarioPresetIntersectionIds(next.scenario_preset_id, scenarioPresets.value),
    )
  } catch (error) {
    if (!props.healthReady || !intersection.value) {
      applyConfiguration(next, onApplied)
      feedback.value = '扰动草稿已保存，后端恢复后可启动仿真'
      return
    }
    feedback.value = error instanceof Error ? error.message : '无法校验配置参数'
    return
  }
  const changesComparison = comparisonChangeRequiresConfirmation(
    props.activeComparisonFingerprint,
    fingerprint,
    props.hasActiveComparisonData,
  )
  if (!changesComparison) {
    applyConfiguration(next, onApplied)
    return
  }
  emit('configChangeRequested', {
    fingerprint,
    differences: comparisonContractDifferences(
      props.activeComparisonFingerprint,
      fingerprint,
    ),
    apply: () => applyConfiguration(next, onApplied),
  })
}

function requestFieldChange(key: string, value: unknown): void {
  const model = fieldModel(key)
  const next = { ...config.value, [model]: value } as CompactScenarioConfig
  let onApplied: (() => void) | undefined
  if (model === 'scenario_preset_id') {
    const intersectionIds = scenarioPresetIntersectionIds(String(value), scenarioPresets.value)
    const reconciliation = reconcileEventsForScenario(next.disturbance_events, intersectionIds)
    if (reconciliation.removedIntersectionIds.length > 0) {
      const removedLabels = reconciliation.removedIntersectionIds.map(formatIntersectionLabel).join('、')
      const confirmed = window.confirm(
        `切换场景将移除不属于新场景的目标：${removedLabels}`
        + (reconciliation.removedEventCount > 0 ? `，并删除 ${reconciliation.removedEventCount} 个空事件。` : '。')
        + '\n是否继续？',
      )
      if (!confirmed) return
    }
    next.disturbance_events = reconciliation.events
    onApplied = () => {
      if (intersectionIds.length > 0 && !intersectionIds.includes(activeIntersectionId.value)) {
        selectIntersection(intersectionIds[0])
      }
    }
  }
  if (model === 'flow_mode') {
    const time = defaultSimulationTimeWindow(next.flow_mode)
    next.simulation_start_time = time.start
    next.simulation_end_time = time.end
    next.disturbance_events = next.disturbance_events.map((event) => ({
      ...event,
      start_time: time.start,
      end_time: time.end,
    }))
    if (next.control_mode === 'ippo' && next.flow_mode !== 'flat') {
      next.control_mode = 'fixed'
      feedback.value = '当前 IPPO 模型仅兼容平峰拓扑，已切换为固定配时'
    }
  }
  requestConfiguration(next, onApplied)
}

function disturbanceEventLabel(event: CompactDisturbanceEvent): string {
  return DISTURBANCE_EVENT_OPTIONS.find((item) => item.value === event.preset_id)?.label
    ?? event.event_type
}

function openDisturbanceModal(event?: CompactDisturbanceEvent): void {
  if (isSessionActive.value) return
  editingDisturbanceId.value = event?.event_id ?? null
  disturbanceDraft.value = {
    presetId: event?.preset_id ?? DISTURBANCE_EVENT_OPTIONS[0].value,
    intersectionIds: event
      ? [...event.intersection_ids]
      : disturbanceIntersectionOptions.value.slice(0, 1).map((item) => item.value),
    startTime: event?.start_time ?? config.value.simulation_start_time,
    endTime: event?.end_time ?? config.value.simulation_end_time,
    vehicleCount: event?.vehicle_count ?? DEFAULT_MAJOR_EVENT_VEHICLE_COUNT,
  }
  disturbanceFormError.value = ''
  disturbanceModalOpen.value = true
}

function closeDisturbanceModal(): void {
  disturbanceModalOpen.value = false
  editingDisturbanceId.value = null
  disturbanceFormError.value = ''
}

function selectAllDisturbanceIntersections(): void {
  disturbanceDraft.value.intersectionIds = disturbanceIntersectionOptions.value.map((item) => item.value)
}

function clearDisturbanceIntersections(): void {
  disturbanceDraft.value.intersectionIds = []
}

function selectDisturbanceType(presetId: DisturbancePresetId): void {
  const option = availableDisturbanceEventOptions.value.find((item) => item.value === presetId)
  if (!option || option.disabled) return
  disturbanceDraft.value.presetId = presetId
  if (
    (option.eventType === 'major_event_opening' || option.eventType === 'major_event_closing')
    && (
      !Number.isInteger(disturbanceDraft.value.vehicleCount)
      || disturbanceDraft.value.vehicleCount < MIN_MAJOR_EVENT_VEHICLE_COUNT
      || disturbanceDraft.value.vehicleCount > MAX_MAJOR_EVENT_VEHICLE_COUNT
    )
  ) disturbanceDraft.value.vehicleCount = DEFAULT_MAJOR_EVENT_VEHICLE_COUNT
  disturbanceFormError.value = ''
}

function saveDisturbanceEvent(): void {
  const option = DISTURBANCE_EVENT_OPTIONS.find((item) => item.value === disturbanceDraft.value.presetId)
  if (!option) {
    disturbanceFormError.value = '请选择扰动事件类型'
    return
  }
  if (!eventTypes.value.includes(option.eventType)) {
    disturbanceFormError.value = `后端未提供事件类型：${option.label}`
    return
  }
  if (disturbanceDraft.value.intersectionIds.length === 0) {
    disturbanceFormError.value = '请选择至少一个路口'
    return
  }
  const availableIntersections = new Set(disturbanceIntersectionOptions.value.map((item) => item.value))
  const invalidIntersections = disturbanceDraft.value.intersectionIds.filter(
    (intersectionId) => !availableIntersections.has(intersectionId),
  )
  if (invalidIntersections.length > 0) {
    disturbanceFormError.value = `以下路口不属于当前场景：${invalidIntersections.map(formatIntersectionLabel).join('、')}`
    return
  }
  if (
    isMajorDisturbance.value
    && (
      !Number.isInteger(disturbanceDraft.value.vehicleCount)
      || disturbanceDraft.value.vehicleCount < MIN_MAJOR_EVENT_VEHICLE_COUNT
      || disturbanceDraft.value.vehicleCount > MAX_MAJOR_EVENT_VEHICLE_COUNT
    )
  ) {
    disturbanceFormError.value = `活动车辆数必须为 ${MIN_MAJOR_EVENT_VEHICLE_COUNT}-${MAX_MAJOR_EVENT_VEHICLE_COUNT} 的整数`
    return
  }
  const outerStart = clockTimeToMinutes(config.value.simulation_start_time)
  const outerEnd = clockTimeToMinutes(config.value.simulation_end_time)
  const eventStart = clockTimeToMinutes(disturbanceDraft.value.startTime)
  const eventEnd = clockTimeToMinutes(disturbanceDraft.value.endTime)
  if (
    !Number.isFinite(eventStart)
    || !Number.isFinite(eventEnd)
    || eventStart < outerStart
    || eventStart >= eventEnd
    || eventEnd > outerEnd
  ) {
    disturbanceFormError.value = `事件时间必须位于 ${config.value.simulation_start_time}-${config.value.simulation_end_time} 内`
    return
  }
  const eventId = editingDisturbanceId.value
    ?? `ui_${option.value}_${Date.now()}_${config.value.disturbance_events.length + 1}`
  const nextEvent: CompactDisturbanceEvent = {
    event_id: eventId,
    preset_id: option.value,
    event_type: option.eventType,
    intersection_ids: [...new Set(disturbanceDraft.value.intersectionIds)],
    start_time: disturbanceDraft.value.startTime,
    end_time: disturbanceDraft.value.endTime,
    ...(isMajorDisturbance.value ? { vehicle_count: disturbanceDraft.value.vehicleCount } : {}),
  }
  const existingIndex = config.value.disturbance_events.findIndex((event) => event.event_id === eventId)
  const disturbanceEvents = config.value.disturbance_events.map((event) => ({
    ...event,
    intersection_ids: [...event.intersection_ids],
  }))
  if (existingIndex >= 0) disturbanceEvents.splice(existingIndex, 1, nextEvent)
  else disturbanceEvents.push(nextEvent)
  requestConfiguration(
    { ...config.value, disturbance_events: disturbanceEvents },
    closeDisturbanceModal,
  )
}

function removeDisturbanceEvent(eventId: string): void {
  requestConfiguration({
    ...config.value,
    disturbance_events: config.value.disturbance_events.filter((event) => event.event_id !== eventId),
  })
  if (editingDisturbanceId.value === eventId) {
    editingDisturbanceId.value = null
    disturbanceDraft.value = {
      presetId: DISTURBANCE_EVENT_OPTIONS[0].value,
      intersectionIds: disturbanceIntersectionOptions.value.slice(0, 1).map((item) => item.value),
      startTime: config.value.simulation_start_time,
      endTime: config.value.simulation_end_time,
      vehicleCount: DEFAULT_MAJOR_EVENT_VEHICLE_COUNT,
    }
  }
}

function requestTimeChange(key: 'simulation_start_time' | 'simulation_end_time', value: string): void {
  const next = { ...config.value, [key]: value }
  if (key === 'simulation_start_time') {
    const startMinutes = clockTimeToMinutes(value)
    const endMinutes = clockTimeToMinutes(next.simulation_end_time)
    const maximumEnd = maximumSimulationEndTime(next.flow_mode, value)
    if (endMinutes <= startMinutes || endMinutes > clockTimeToMinutes(maximumEnd)) {
      next.simulation_end_time = maximumEnd
    }
  }
  const outerStart = key === 'simulation_start_time' ? value : next.simulation_start_time
  const outerEnd = key === 'simulation_end_time' ? value : next.simulation_end_time
  const outerStartMinutes = clockTimeToMinutes(outerStart)
  const outerEndMinutes = clockTimeToMinutes(outerEnd)
  next.disturbance_events = next.disturbance_events.map((event) => {
    let startTime = clockTimeToMinutes(event.start_time) < outerStartMinutes
      ? outerStart
      : event.start_time
    let endTime = clockTimeToMinutes(event.end_time) > outerEndMinutes
      ? outerEnd
      : event.end_time
    if (clockTimeToMinutes(startTime) >= clockTimeToMinutes(endTime)) {
      startTime = outerStart
      endTime = outerEnd
    }
    return { ...event, start_time: startTime, end_time: endTime }
  })
  requestConfiguration(next)
}

function updateDisturbanceStartTime(value: string): void {
  disturbanceDraft.value.startTime = value
  const end = clockTimeToMinutes(disturbanceDraft.value.endTime)
  const start = clockTimeToMinutes(value)
  if (end <= start) {
    disturbanceDraft.value.endTime = minutesToClockTime(Math.min(
      clockTimeToMinutes(config.value.simulation_end_time),
      start + 1,
    ))
  }
  disturbanceFormError.value = ''
}

function updateDisturbanceEndTime(value: string): void {
  disturbanceDraft.value.endTime = value
  disturbanceFormError.value = ''
}

function requestControlModeChange(value: string): void {
  const option = controlModeOptions.value.find((item) => item.value === value)
  if (!option || option.disabled) return
  requestConfiguration({ ...config.value, control_mode: value })
}

function saveConfig() {
  try {
    const blob = new Blob([JSON.stringify(buildExport(), null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${config.value.scenario_preset_id}-scene-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
    feedback.value = '仿真场景配置已保存'
  }
  catch (error) { feedback.value = error instanceof Error ? error.message : '场景保存失败' }
}
async function exportConfig() {
  if (presetUnavailableMessage.value) {
    feedback.value = presetUnavailableMessage.value
    return
  }
  exporting.value = true
  try {
    const payload = buildPayload()
    const { blob, filename } = await exportScenarioArchive(payload)
    const validation = await validateScenarioArchive(blob, {
      scenarioPresetId: payload.scenario_preset_id,
      period: payload.period,
      controlMode: payload.control_mode,
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename ?? `${config.value.scenario_preset_id}-${Date.now()}.zip`
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
    feedback.value = validation.summary
  } catch (error) {
    feedback.value = simulationApiErrorMessage(error, '场景导出失败')
  } finally {
    exporting.value = false
  }
}
function handleStart() {
  multiplierOpen.value = false
  if (presetUnavailableMessage.value) {
    feedback.value = presetUnavailableMessage.value
    return
  }
  if (!isIntersectionSupported.value) {
    feedback.value = unsupportedMessage.value
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

      <button v-if="statusMessage" type="button" class="left-sidebar__status-dot" :class="{ 'is-feedback': feedback }" :title="statusMessage" :aria-label="statusMessage" @click="dismissStatusMessage" />

      <label
        v-for="(field, index) in fields"
        :key="field.key"
        class="left-sidebar__field"
        :style="{ left: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[index].left}px`, top: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[index].top}px`, width: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[index].width}px` }"
      >
        <span class="left-sidebar__field-label">{{ field.label }}</span>
        <el-select
          :model-value="config[fieldModel(field.key)]"
          :disabled="isSessionActive"
          class="left-sidebar__select"
          popper-class="left-sidebar-select-popper"
          @change="requestFieldChange(field.key, $event)"
        >
          <el-option
            v-for="option in field.options"
            :key="option.value"
            :label="option.label"
            :value="option.value"
            :disabled="'disabled' in option && option.disabled"
          />
        </el-select>
      </label>

      <div
        class="left-sidebar__disturbance"
        :style="{
          left: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.fields[2].left}px`,
          top: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.disturbanceTargets.top}px`,
          width: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.timeRange.width}px`,
        }"
      >
        <span class="left-sidebar__field-label">扰动事件</span>
        <button
          type="button"
          class="left-sidebar__disturbance-button"
          :disabled="isSessionActive || disturbanceIntersectionOptions.length === 0"
          @click="openDisturbanceModal()"
        >
          <span>{{ config.disturbance_events.length > 0 ? `已配置 ${config.disturbance_events.length} 项` : '新增扰动事件' }}</span>
          <i aria-hidden="true">+</i>
        </button>
      </div>

      <div
        class="left-sidebar__field left-sidebar__field--time"
        :style="{
          left: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.timeRange.left}px`,
          top: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.timeRange.top}px`,
          width: `${LEFT_SIDEBAR_REFERENCE_LAYOUT.timeRange.width}px`,
        }"
      >
        <span class="left-sidebar__field-label">仿真展示时间</span>
        <div class="left-sidebar__time-parts">
          <label class="left-sidebar__time-stepper-field">
            <span>开始时间</span>
            <HourMinuteStepper
              :model-value="config.simulation_start_time"
              :minimum="simulationStartMinimum"
              :maximum="simulationStartMaximum"
              :disabled="isSessionActive"
              label="开始时间"
              @update:model-value="requestTimeChange('simulation_start_time', $event)"
            />
          </label>
          <label class="left-sidebar__time-stepper-field">
            <span>结束时间</span>
            <HourMinuteStepper
              :model-value="config.simulation_end_time"
              :minimum="simulationEndMinimum"
              :maximum="simulationEndMaximum"
              :disabled="isSessionActive"
              label="结束时间"
              @update:model-value="requestTimeChange('simulation_end_time', $event)"
            />
          </label>
        </div>
      </div>

      <div class="left-sidebar__config-summary" :title="configNote">
        <span v-for="line in configNote.split('\n')" :key="line">{{ line }}</span>
      </div>

      <div class="left-sidebar__file-actions">
        <button type="button" @click="saveConfig">保存仿真场景</button>
        <button type="button" :disabled="exporting" @click="exportConfig">{{ exporting ? '导出中…' : '导出当前仿真场景' }}</button>
      </div>

      <LeftSidebarSectionHeader title="管控算法选择" variant="algorithm" />
      <div class="left-sidebar__algorithm-select">
        <el-select
          :model-value="config.control_mode"
          :disabled="isSessionActive"
          aria-label="管控算法选择"
          popper-class="left-sidebar-algorithm-popper"
          @change="requestControlModeChange($event as string)"
        >
          <el-option
            v-for="option in controlModeOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
            :disabled="option.disabled"
            :title="controlModeOptionTitle(option)"
          />
        </el-select>
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
            :disabled="props.controlling || props.state === 'QUEUED'"
            :aria-expanded="multiplierOpen"
            aria-haspopup="listbox"
            :title="playbackSpeedTitle"
            :aria-label="playbackSpeedTitle"
          >
            <span>×{{ config.playback_speed }}</span>
          </button>
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
          <em>{{ algorithmLabel }}</em>
          <button
            v-if="state === 'FAILED'"
            type="button"
            class="left-sidebar__runtime-error-button"
            @click="runtimeErrorOpen = true"
          >查看原因</button>
        </div>
        <dl>
          <div><dt>仿真时间</dt><dd>{{ officialTimeLabel }}</dd></div>
          <div><dt>活动车辆</dt><dd>{{ activeVehicleLabel }}</dd></div>
          <div><dt>实际推进</dt><dd>{{ achievedPlaybackLabel }}</dd></div>
        </dl>
        <p v-if="playbackBusy" class="left-sidebar__runtime-warning">仿真计算繁忙，时间推进慢于墙钟时间</p>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="runtimeErrorOpen"
        class="runtime-error-modal"
        role="presentation"
        @mousedown.self="runtimeErrorOpen = false"
      >
        <section
          class="runtime-error-modal__dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="runtime-error-title"
        >
          <header>
            <div>
              <span>仿真失败详情</span>
              <h2 id="runtime-error-title">{{ algorithmLabel }}</h2>
            </div>
            <button type="button" aria-label="关闭失败详情" title="关闭" @click="runtimeErrorOpen = false">×</button>
          </header>
          <dl>
            <div><dt>失败阶段</dt><dd>{{ runtimeFailureStage }}</dd></div>
            <div><dt>会话状态</dt><dd>{{ stateLabel }}</dd></div>
          </dl>
          <p>{{ statusError || '仿真运行失败，请检查后端原始错误。' }}</p>
          <details open>
            <summary>后端原始错误</summary>
            <pre>{{ runtimeRawError }}</pre>
          </details>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="disturbanceModalOpen"
        class="disturbance-modal"
        role="presentation"
        @mousedown.self="closeDisturbanceModal"
      >
        <section
          class="disturbance-modal__dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="disturbance-modal-title"
        >
          <header class="disturbance-modal__header">
            <h2 id="disturbance-modal-title">{{ editingDisturbanceId ? '编辑扰动事件' : '新增扰动事件' }}</h2>
            <button type="button" aria-label="关闭" title="关闭" @click="closeDisturbanceModal">×</button>
          </header>

          <div v-if="config.disturbance_events.length > 0" class="disturbance-modal__configured">
            <span>已配置事件</span>
            <div
              v-for="event in config.disturbance_events"
              :key="event.event_id"
              class="disturbance-modal__event-row"
              :class="{ 'is-editing': editingDisturbanceId === event.event_id }"
            >
              <button type="button" @click="openDisturbanceModal(event)">
                <strong>{{ disturbanceEventLabel(event) }}</strong>
                <small>{{ event.start_time }}-{{ event.end_time }} · {{ event.intersection_ids.map(formatIntersectionLabel).join('、') }}<template v-if="event.vehicle_count"> · 每路口 {{ event.vehicle_count }} 辆</template></small>
              </button>
              <button type="button" aria-label="删除扰动事件" title="删除" @click="removeDisturbanceEvent(event.event_id)">×</button>
            </div>
          </div>

          <div class="disturbance-modal__form">
            <div class="disturbance-modal__event-type">
              <span>事件类型</span>
              <div role="radiogroup" aria-label="扰动事件类型">
                <button
                  v-for="option in availableDisturbanceEventOptions"
                  :key="option.value"
                  type="button"
                  role="radio"
                  :aria-checked="disturbanceDraft.presetId === option.value"
                  :class="{ 'is-selected': disturbanceDraft.presetId === option.value }"
                  :disabled="option.disabled"
                  @click="selectDisturbanceType(option.value)"
                >{{ option.label }}</button>
              </div>
            </div>
            <div class="disturbance-modal__event-settings">
            <label>
              <span>开始时间</span>
              <HourMinuteStepper
                :model-value="disturbanceDraft.startTime"
                :minimum="disturbanceStartMinimum"
                :maximum="disturbanceStartMaximum"
                label="扰动开始时间"
                @update:model-value="updateDisturbanceStartTime"
              />
            </label>

            <label>
              <span>结束时间</span>
              <HourMinuteStepper
                :model-value="disturbanceDraft.endTime"
                :minimum="disturbanceEndMinimum"
                :maximum="disturbanceEndMaximum"
                label="扰动结束时间"
                @update:model-value="updateDisturbanceEndTime"
              />
            </label>
            <label v-if="isMajorDisturbance" class="disturbance-modal__vehicle-count">
              <span>每个路口活动车辆数</span>
              <el-input-number
                v-model="disturbanceDraft.vehicleCount"
                :min="MIN_MAJOR_EVENT_VEHICLE_COUNT"
                :max="MAX_MAJOR_EVENT_VEHICLE_COUNT"
                :step="1"
                :precision="0"
                controls-position="right"
              />
            </label>
            </div>

            <fieldset>
              <div class="disturbance-modal__intersection-tools">
                <legend>影响路口 · 已选 {{ selectedDisturbanceIntersectionCount }}</legend>
                <button type="button" @click="selectAllDisturbanceIntersections">全选</button>
                <button type="button" @click="clearDisturbanceIntersections">清空</button>
              </div>
              <el-checkbox-group v-model="disturbanceDraft.intersectionIds" class="disturbance-modal__intersections">
                <el-checkbox
                  v-for="option in disturbanceIntersectionOptions"
                  :key="option.value"
                  :value="option.value"
                >{{ option.label }}</el-checkbox>
              </el-checkbox-group>
            </fieldset>
          </div>

          <p v-if="disturbanceFormError" class="disturbance-modal__error">{{ disturbanceFormError }}</p>
          <footer class="disturbance-modal__footer">
            <button type="button" @click="closeDisturbanceModal">取消</button>
            <button type="button" class="is-primary" @click="saveDisturbanceEvent">{{ editingDisturbanceId ? '保存修改' : '添加事件' }}</button>
          </footer>
        </section>
      </div>
    </Teleport>
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
.left-sidebar__select :deep(.el-tag) {
  max-width: 102px;
  border-color: rgba(82, 194, 250, .42);
  background: rgba(20, 103, 169, .42);
  color: #effaff;
}
.left-sidebar__disturbance {
  position: absolute;
  z-index: 3;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.left-sidebar__disturbance-button {
  width: 100%;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  border: 1px solid rgba(27, 126, 242, .45);
  border-radius: 5px;
  background: linear-gradient(90deg, #043563, #03315b);
  color: #fff;
  font: 600 15px/1 inherit;
  cursor: pointer;
}
.left-sidebar__disturbance-button i {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(82, 194, 250, .65);
  border-radius: 50%;
  color: #ffe47a;
  font-size: 17px;
  font-style: normal;
}
.left-sidebar__disturbance-button:hover:not(:disabled),
.left-sidebar__disturbance-button:focus-visible {
  border-color: #52c2fa;
  box-shadow: 0 0 8px rgba(33, 230, 255, .24);
  outline: none;
}
.left-sidebar__disturbance-button:disabled { opacity: .46; cursor: not-allowed; }
.left-sidebar__time-parts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.left-sidebar__time-stepper-field {
  min-width: 0;
  display: grid;
  gap: 4px;
}
.left-sidebar__time-stepper-field > span {
  color: #83b9d7;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.left-sidebar__time-parts :deep(.hour-minute-stepper) { width: 100%; height: 34px; }

:global(.left-sidebar-algorithm-popper.el-popper) {
  border: 1px solid rgba(82, 194, 250, .62) !important;
  border-radius: 6px !important;
  background: #061a31 !important;
  box-shadow: 0 10px 24px rgba(0, 6, 18, .58), inset 0 0 16px rgba(33, 139, 255, .08) !important;
}
:global(.left-sidebar-algorithm-popper .el-select-dropdown__item) {
  color: #cce9f7;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
:global(.left-sidebar-algorithm-popper .el-select-dropdown__item.is-hovering),
:global(.left-sidebar-algorithm-popper .el-select-dropdown__item.is-selected) {
  background: rgba(33, 139, 255, .24);
  color: #fff;
}
:global(.left-sidebar-algorithm-popper .el-select-dropdown__item.is-disabled) {
  color: #66879d;
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

.left-sidebar__algorithm-select {
  position: absolute;
  z-index: 3;
  left: 35px;
  top: 546px;
  width: 328px;
}
.left-sidebar__algorithm-select :deep(.el-select) { width: 100%; }
.left-sidebar__algorithm-select :deep(.el-select__wrapper) {
  min-height: 40px;
  padding: 4px 14px;
  border: 1px solid rgba(82, 194, 250, .55);
  border-radius: 5px;
  background: linear-gradient(90deg, #043563, #03315b);
  box-shadow: inset 0 0 12px rgba(33, 230, 255, .06);
}
.left-sidebar__algorithm-select :deep(.el-select__selected-item) {
  color: #fff;
  font-size: 14px;
  font-weight: 600;
}

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
  left: 32px; top: 345px; width: 330px; height: 43px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 0; padding: 5px 12px; border-color: rgba(98,190,255,.7);
  background: linear-gradient(180deg,rgba(3,38,73,.88),rgba(1,20,46,.82)); color: #edf8ff;
  font-size: 12px; font-weight: 600; line-height: 1.35; white-space: nowrap;
}
.left-sidebar__file-actions {
  position: absolute; z-index: 4; left: 28px; top: 400px; width: 334px; height: 38px;
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
.left-sidebar__progress { left: 25px; top: 680px; width: 263px; height: 3px; }
.left-sidebar__progress-knob { width: 7px; height: 7px; }
.left-sidebar__speed-badge {
  position: absolute; z-index: 12; left: 295px; top: 663px; width: 88px; height: 34px; display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 0; border: 1px solid rgba(89,147,255,.7); border-radius: 18px; background: rgba(2,19,42,.9);
  box-shadow: inset 0 0 9px rgba(33,139,255,.14), 0 0 7px rgba(33,139,255,.12);
  color: #fff; font: 600 14px/1 inherit; cursor: pointer;
}
.left-sidebar__speed-badge small { color: #74dfff; font-size: 9px; font-weight: 600; white-space: nowrap; }
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
.left-sidebar__runtime-head em { min-width: 0; margin-left: auto; overflow: hidden; color: #8fc6e5; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
.left-sidebar__runtime-error-button {
  flex: 0 0 auto; min-width: 54px; height: 20px; padding: 0 7px; border: 1px solid rgba(255,107,107,.58);
  background: rgba(77,12,22,.62); color: #ffd2d2; font-size: 9px; cursor: pointer;
}
.left-sidebar__runtime-error-button:hover,
.left-sidebar__runtime-error-button:focus-visible { border-color: #ff8d8d; box-shadow: 0 0 8px rgba(255,107,107,.35); outline: none; }
.left-sidebar__runtime.is-running .left-sidebar__runtime-head strong { color: #58f0ae; }
.left-sidebar__runtime.is-running .left-sidebar__runtime-head strong i { background: #3ce69a; }
.left-sidebar__runtime.is-queued .left-sidebar__runtime-head strong { color: #7fdfff; }
.left-sidebar__runtime.is-queued .left-sidebar__runtime-head strong i { background: #52c2fa; box-shadow: 0 0 8px #52c2fa; }
.left-sidebar__runtime.is-starting .left-sidebar__runtime-head strong,
.left-sidebar__runtime.is-stopping .left-sidebar__runtime-head strong { color: #ffe47a; }
.left-sidebar__runtime.is-starting .left-sidebar__runtime-head strong i,
.left-sidebar__runtime.is-stopping .left-sidebar__runtime-head strong i { background: #e8b94c; }
.left-sidebar__runtime.is-failed .left-sidebar__runtime-head strong { color: #ff9d9d; }
.left-sidebar__runtime.is-failed .left-sidebar__runtime-head strong i { background: #ff6b6b; }
.left-sidebar__runtime dl { height: 39px; display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 12px; margin: 3px 0 0; }
.left-sidebar__runtime dl div { min-width: 0; }
.left-sidebar__runtime dt { color: #668fa9; font-size: 9px; white-space: nowrap; }
.left-sidebar__runtime dd { margin: 3px 0 0; overflow: hidden; color: #eefaff; font-size: 11px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.left-sidebar__runtime-warning { margin: 0; color: #ffd879; font-size: 9px; line-height: 1.2; white-space: nowrap; }

.runtime-error-modal {
  position: fixed; inset: 0; z-index: 3200; display: grid; place-items: center; padding: 24px;
  background: rgba(0,8,20,.7); backdrop-filter: blur(4px);
}
.runtime-error-modal__dialog {
  width: min(620px, calc(100vw - 48px)); max-height: min(620px, calc(100vh - 48px)); overflow: auto;
  padding: 24px 28px; border: 1px solid rgba(95,194,255,.72);
  clip-path: polygon(14px 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%, 0 14px);
  background: #071a30; box-shadow: 0 18px 60px rgba(0,0,0,.5), inset 0 0 36px rgba(39,131,214,.12);
  color: #eaf7ff; font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.runtime-error-modal__dialog header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.runtime-error-modal__dialog header span { color: #73cfff; font-size: 11px; }
.runtime-error-modal__dialog h2 { margin: 5px 0 0; font-size: 19px; letter-spacing: 0; }
.runtime-error-modal__dialog header > button {
  width: 30px; height: 30px; padding: 0; border: 1px solid rgba(115,207,255,.55); border-radius: 50%;
  background: #03101f; color: #d9f4ff; font-size: 20px; cursor: pointer;
}
.runtime-error-modal__dialog dl { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px; margin: 22px 0 0; }
.runtime-error-modal__dialog dl div { padding: 10px 12px; border: 1px solid rgba(79,148,199,.25); background: rgba(3,14,29,.55); }
.runtime-error-modal__dialog dt { color: #6d9fbd; font-size: 10px; }
.runtime-error-modal__dialog dd { margin: 4px 0 0; color: #fff; font-size: 13px; }
.runtime-error-modal__dialog p { margin: 16px 0; color: #ffb0b0; font-size: 13px; line-height: 1.6; }
.runtime-error-modal__dialog details { border-top: 1px solid rgba(79,148,199,.25); padding-top: 14px; }
.runtime-error-modal__dialog summary { color: #91cde9; font-size: 11px; cursor: pointer; }
.runtime-error-modal__dialog pre {
  max-height: 240px; overflow: auto; margin: 10px 0 0; padding: 12px; background: #020b15;
  color: #c8e6f5; font: 11px/1.55 Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere;
}

.disturbance-modal {
  position: fixed;
  inset: var(--dashboard-top-offset, 92px) 0 var(--dashboard-bottom-offset, 92px);
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(1, 10, 24, .34);
  backdrop-filter: blur(3px);
}
.disturbance-modal__dialog {
  position: relative;
  width: min(1000px, calc(100vw - 48px));
  height: min(650px, calc(100vh - 120px));
  min-height: 520px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(91, 159, 255, .72);
  clip-path: polygon(18px 0, 35% 0, 37% 22px, 63% 22px, 65% 0, calc(100% - 18px) 0, 100% 18px, 100% calc(100% - 18px), calc(100% - 18px) 100%, 18px 100%, 0 calc(100% - 18px), 0 18px);
  background: linear-gradient(180deg, rgba(20, 48, 89, .97), rgba(24, 70, 125, .96));
  box-shadow: inset 0 0 42px rgba(69, 136, 225, .18), 0 0 26px rgba(18, 110, 218, .24);
  color: #f4fbff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.disturbance-modal__dialog::before,
.disturbance-modal__dialog::after {
  content: '';
  position: absolute;
  top: 0;
  width: 35%;
  height: 2px;
  background: linear-gradient(90deg, transparent, #5ad9ff);
  box-shadow: 0 0 8px rgba(90, 217, 255, .85);
}
.disturbance-modal__dialog::before { left: 0; }
.disturbance-modal__dialog::after { right: 0; transform: scaleX(-1); }
.disturbance-modal__header {
  flex: 0 0 58px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 38px 0;
}
.disturbance-modal__header h2 { margin: 0; font-size: 20px; letter-spacing: 0; }
.disturbance-modal__header > button {
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid rgba(98, 216, 255, .45);
  border-radius: 50%;
  background: rgba(2, 21, 44, .72);
  color: #ccefff;
  font-size: 21px;
  line-height: 1;
  cursor: pointer;
}
.disturbance-modal__header > button:hover,
.disturbance-modal__header > button:focus-visible {
  border-color: #62d8ff;
  box-shadow: 0 0 10px rgba(33, 230, 255, .45);
  outline: none;
}
.disturbance-modal__configured {
  flex: 0 0 auto;
  max-height: 132px;
  overflow-y: auto;
  padding: 8px 38px 0;
}
.disturbance-modal__configured > span,
.disturbance-modal__form label > span,
.disturbance-modal__form legend {
  display: block;
  margin-bottom: 8px;
  color: #9fc9df;
  font-size: 12px;
}
.disturbance-modal__event-row {
  min-height: 48px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px;
  align-items: stretch;
  margin-top: 6px;
  border: 1px solid rgba(82, 194, 250, .2);
  border-radius: 4px;
  background: #08223d;
}
.disturbance-modal__event-row.is-editing { border-color: #52c2fa; }
.disturbance-modal__event-row button {
  border: 0;
  background: transparent;
  color: #eaf8ff;
  cursor: pointer;
}
.disturbance-modal__event-row button:first-child {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  text-align: left;
}
.disturbance-modal__event-row strong { flex: 0 0 auto; font-size: 13px; }
.disturbance-modal__event-row small {
  min-width: 0;
  overflow: hidden;
  color: #84b8d5;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.disturbance-modal__event-row button:last-child { color: #ff9eaa; font-size: 20px; }
.disturbance-modal__form {
  min-height: 0;
  flex: 1 1 auto;
  display: grid;
  gap: 18px;
  overflow-y: auto;
  padding: 18px 38px 10px;
}
.disturbance-modal__form label { display: block; }
.disturbance-modal__event-type > span,
.disturbance-modal__event-settings label > span {
  display: block;
  margin-bottom: 8px;
  color: #a8cfe4;
  font-size: 12px;
}
.disturbance-modal__event-type > div {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 7px;
}
.disturbance-modal__event-type button {
  min-width: 0;
  height: 38px;
  padding: 0 5px;
  border: 1px solid rgba(82, 194, 250, .25);
  border-radius: 4px;
  background: #08223d;
  color: #b9d9ea;
  font: 600 12px/1.2 inherit;
  cursor: pointer;
}
.disturbance-modal__event-type button.is-selected {
  border-color: #52c2fa;
  background: #17649a;
  color: #fff;
  box-shadow: inset 0 0 12px rgba(74, 210, 255, .16), 0 0 8px rgba(27, 126, 242, .2);
}
.disturbance-modal__event-type button:disabled { opacity: .38; cursor: not-allowed; }
.disturbance-modal__event-settings {
  display: grid;
  grid-template-columns: repeat(2, minmax(130px, 1fr));
  gap: 12px;
}
.disturbance-modal__event-settings :deep(.hour-minute-stepper) { width: 100%; height: 40px; }
.disturbance-modal__vehicle-count { grid-column: 1 / -1; max-width: 260px; }
.disturbance-modal__vehicle-count :deep(.el-input-number) { width: 100%; }
.disturbance-modal__vehicle-count :deep(.el-input__wrapper) {
  min-height: 40px;
  border: 1px solid rgba(82, 194, 250, .38);
  border-radius: 4px;
  background: #092846;
  box-shadow: none;
}
.disturbance-modal__vehicle-count :deep(.el-input__inner) { color: #fff; }
.disturbance-modal__vehicle-count :deep(.el-input-number__decrease),
.disturbance-modal__vehicle-count :deep(.el-input-number__increase) {
  border-color: rgba(82, 194, 250, .22);
  background: #071f38;
  color: #9edcf2;
}
.disturbance-modal__vehicle-count :deep(.el-input-number__decrease:hover),
.disturbance-modal__vehicle-count :deep(.el-input-number__increase:hover) {
  color: #fff;
}
.disturbance-modal__form :deep(.el-select) { width: 100%; }
.disturbance-modal__form :deep(.el-select__wrapper) {
  min-height: 40px;
  border: 1px solid rgba(82, 194, 250, .38);
  border-radius: 4px;
  background: #092846;
  box-shadow: none;
}
.disturbance-modal__form :deep(.el-select__selected-item) { color: #fff; }
.disturbance-modal__form fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.disturbance-modal__intersection-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.disturbance-modal__intersection-tools legend { margin: 0 auto 0 0; }
.disturbance-modal__intersection-tools button {
  min-width: 52px;
  height: 28px;
  border: 1px solid rgba(82, 194, 250, .34);
  border-radius: 4px;
  background: #092846;
  color: #bfe8f8;
  cursor: pointer;
}
.disturbance-modal__intersections {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.disturbance-modal__intersections :deep(.el-checkbox) {
  height: 34px;
  display: flex;
  justify-content: center;
  margin: 0;
  padding: 0 9px;
  border: 1px solid rgba(82, 194, 250, .2);
  border-radius: 4px;
  background: #08223d;
}
.disturbance-modal__intersections :deep(.el-checkbox.is-checked) {
  border-color: #52c2fa;
  background: #17649a;
  box-shadow: inset 0 0 12px rgba(74, 210, 255, .15);
}
.disturbance-modal__intersections :deep(.el-checkbox__input) { display: none; }
.disturbance-modal__intersections :deep(.el-checkbox__label) { padding-left: 0; }
.disturbance-modal__intersections :deep(.el-checkbox__label) { color: #cce9f7; font-size: 12px; }
.disturbance-modal__intersections :deep(.el-checkbox__input.is-checked + .el-checkbox__label) { color: #fff; }
.disturbance-modal__error { margin: 0 38px; color: #ffb2bc; font-size: 12px; }
.disturbance-modal__footer {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 38px 24px;
}
.disturbance-modal__footer button {
  min-width: 92px;
  height: 36px;
  border: 1px solid rgba(82, 194, 250, .42);
  border-radius: 4px;
  background: #0a2947;
  color: #cae7f5;
  font: 600 13px/1 inherit;
  cursor: pointer;
}
.disturbance-modal__footer button.is-primary {
  border-color: #52c2fa;
  background: #2675c8;
  color: #fff;
}
@media (max-width: 560px) {
  .disturbance-modal { inset: 80px 0 0; padding: 12px; }
  .disturbance-modal__dialog { width: calc(100vw - 24px); height: calc(100vh - 104px); min-height: 0; }
  .disturbance-modal__header,
  .disturbance-modal__configured,
  .disturbance-modal__form,
  .disturbance-modal__footer { padding-left: 20px; padding-right: 20px; }
  .disturbance-modal__event-settings { grid-template-columns: 1fr 1fr; }
  .disturbance-modal__event-type > div { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .disturbance-modal__intersections { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (prefers-reduced-motion: reduce) {
  .left-sidebar__progress-fill,
  .left-sidebar__file-actions button,
  .left-sidebar__controls button { transition: none; }
}
</style>
