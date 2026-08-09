import { computed, ref, watch, type Ref } from 'vue'
import {
  DISTURBANCE_EVENT_OPTIONS,
  SCENARIO_MODE_OPTIONS,
  SIMULATION_PERIOD_RANGES,
  SIMULATION_TIME_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
  clockTimeToMinutes,
  defaultSimulationTimeWindow,
  maximumSimulationEndTime,
  simulationTimeWindow,
  type ScenarioModeId,
} from '../constants/scenarioOptions'
import {
  SIMULATION_SNAPSHOT_INTERVAL_MS,
  SUPPORTED_BACKEND_CONTROL_MODES,
  requireAvailableControlMode,
  requirePeriodCompatibleControlMode,
  resolveDashboardControlModes,
} from '../constants/simulationOptions'
import type { CatalogIntersection, CatalogScenarioPreset } from '../types/catalog'
import type { StartSimulationRequest } from '../types/simulation'
import type { TrafficFlowMode } from '../types/scenario'
import { buildStartSimulationRequest } from '../utils/scenarioPayload'
import { scenarioPresetIntersectionIds } from '../utils/scenarioPresetRules'
import {
  SCENARIO_CONFIG_EXPORT_VERSION,
  DEFAULT_MAJOR_EVENT_VEHICLE_COUNT,
  resolveMajorEventVehicleCount,
  resolveImportedDisturbanceTimes,
} from '../utils/scenarioConfigMigration'
import {
  publishScenarioDraft,
  type ScenarioDraftDisturbanceEvent,
} from './useScenarioDraftStore'

export interface CompactScenarioConfig {
  scenario_preset_id: ScenarioModeId
  flow_mode: TrafficFlowMode
  disturbance_events: CompactDisturbanceEvent[]
  simulation_start_time: string
  simulation_end_time: string
  playback_speed: number
  control_mode: string
}

export type CompactDisturbanceEvent = ScenarioDraftDisturbanceEvent

export interface ScenarioConfigExport {
  version: typeof SCENARIO_CONFIG_EXPORT_VERSION
  exported_at: string
  ui_config: CompactScenarioConfig
  display: {
    scenario: string
    disturbance: string
    flow_mode: string
    simulation_time: string
    algorithm: string
  }
  backend_request: StartSimulationRequest
  data_sources: {
    scenario: 'catalog' | 'compatibility_preset'
    disturbance: 'catalog'
    time: 'local_range'
    algorithm: 'backend'
  }
}

const FLOW_MODE_TO_PERIOD: Record<TrafficFlowMode, string> = {
  flat: 'off_peak',
  morning_peak: 'morning_peak',
  evening_peak: 'evening_peak',
}

function defaultCompactConfig(): CompactScenarioConfig {
  const time = defaultSimulationTimeWindow('morning_peak')
  return {
    scenario_preset_id: 'xiongan_20',
    flow_mode: 'morning_peak',
    disturbance_events: [],
    simulation_start_time: time.start,
    simulation_end_time: time.end,
    playback_speed: 1,
    control_mode: 'fixed',
  }
}

function resolvePeriod(config: CompactScenarioConfig, periods: string[]): string {
  const mapped = FLOW_MODE_TO_PERIOD[config.flow_mode]
  return periods.includes(mapped) ? mapped : periods[0] ?? mapped
}

export function buildSimulationPayload(
  config: CompactScenarioConfig,
  _intersection: CatalogIntersection | null,
  periods: string[],
  scenarioPresets: CatalogScenarioPreset[] = [],
  _supportedIntersectionIds: string[] = [],
  controlModes: string[] = [...SUPPORTED_BACKEND_CONTROL_MODES],
): StartSimulationRequest {
  const time = simulationTimeWindow(
    config.flow_mode,
    config.simulation_start_time,
    config.simulation_end_time,
  )
  const presetIntersectionIds = scenarioPresetIntersectionIds(
    config.scenario_preset_id,
    scenarioPresets,
  )
  if (presetIntersectionIds.length === 0) {
    throw new Error(`未知场景预设：${config.scenario_preset_id}`)
  }
  const presetIntersections = new Set(presetIntersectionIds)
  const disturbanceEvents = config.disturbance_events.map((event) => {
    const option = DISTURBANCE_EVENT_OPTIONS.find((item) => item.value === event.preset_id)
    if (!option || option.eventType !== event.event_type) throw new Error('扰动事件不受支持')
    const intersectionIds = [...new Set(event.intersection_ids)]
    if (intersectionIds.length === 0) throw new Error('请选择至少一个扰动路口')
    const invalid = intersectionIds.filter((id) => !presetIntersections.has(id))
    if (invalid.length > 0) throw new Error(`扰动路口不可用于当前场景：${invalid.join(', ')}`)
    const outerStartMinutes = clockTimeToMinutes(config.simulation_start_time)
    const eventStartMinutes = clockTimeToMinutes(event.start_time)
    const eventEndMinutes = clockTimeToMinutes(event.end_time)
    const startSeconds = (eventStartMinutes - outerStartMinutes) * 60
    const endSeconds = (eventEndMinutes - outerStartMinutes) * 60
    if (
      !Number.isFinite(startSeconds)
      || !Number.isFinite(endSeconds)
      || startSeconds < 0
      || startSeconds >= endSeconds
      || endSeconds > time.durationSeconds
    ) {
      throw new Error(`扰动事件“${option.label}”必须位于仿真时间内`)
    }
    return {
      eventId: event.event_id,
      eventType: event.event_type,
      intersectionIds,
      startSeconds,
      endSeconds,
      vehicleCount: event.vehicle_count,
    }
  })
  const period = resolvePeriod(config, periods)
  const controlMode = requirePeriodCompatibleControlMode(
    requireAvailableControlMode(config.control_mode, controlModes),
    period,
  )
  return buildStartSimulationRequest({
    scenarioPresetId: config.scenario_preset_id,
    period,
    windowStartSeconds: time.windowStartSeconds,
    durationSeconds: time.durationSeconds,
    controlMode,
    playbackSpeed: config.playback_speed,
    disturbanceEvents,
    snapshotIntervalSeconds: SIMULATION_SNAPSHOT_INTERVAL_MS / 1_000,
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function legacyPresetTimes(value: unknown): { start: string; end: string } | null {
  if (typeof value !== 'string') return null
  const preset = SIMULATION_TIME_OPTIONS.find((item) => item.value === value)
  if (!preset) return null
  const [start, end] = preset.label.split('-')
  return { start: start.padStart(5, '0'), end: end.padStart(5, '0') }
}

export function useCompactScenarioConfig(
  intersection: Ref<CatalogIntersection | null>,
  periods: Ref<string[]>,
  scenarioPresets: Ref<CatalogScenarioPreset[]>,
  playbackSpeeds: Ref<number[]>,
  supportedIntersectionIds: Ref<string[]>,
  controlModes: Ref<string[]>,
) {
  const config = ref<CompactScenarioConfig>(defaultCompactConfig())
  const activeTimeRange = computed(() => SIMULATION_PERIOD_RANGES[config.value.flow_mode])
  const activeMaximumEndTime = computed(() => maximumSimulationEndTime(
    config.value.flow_mode,
    config.value.simulation_start_time,
  ))

  watch(
    config,
    (value) => publishScenarioDraft({
      disturbanceEvents: value.disturbance_events,
      simulationStartTime: value.simulation_start_time,
      simulationEndTime: value.simulation_end_time,
    }),
    { deep: true, immediate: true },
  )

  watch(
    () => config.value.flow_mode,
    (mode) => {
      const time = defaultSimulationTimeWindow(mode)
      config.value.simulation_start_time = time.start
      config.value.simulation_end_time = time.end
      config.value.disturbance_events = config.value.disturbance_events.map((event) => ({
        ...event,
        start_time: time.start,
        end_time: time.end,
      }))
    },
  )

  watch(
    playbackSpeeds,
    (speeds) => {
      if (speeds.length > 0 && !speeds.includes(config.value.playback_speed)) {
        config.value.playback_speed = speeds.includes(1) ? 1 : speeds[0]
      }
    },
    { immediate: true },
  )

  watch(
    controlModes,
    (modes) => {
      const supported: string[] = resolveDashboardControlModes(modes).map((item) => item.value)
      if (supported.length === 0 || supported.includes(config.value.control_mode)) return
      config.value.control_mode = supported.includes('fixed') ? 'fixed' : supported[0]
    },
    { immediate: true },
  )

  const labels = computed(() => ({
    scenario: scenarioPresets.value.find((item) => item.preset_id === config.value.scenario_preset_id)?.label
      ?? SCENARIO_MODE_OPTIONS.find((item) => item.value === config.value.scenario_preset_id)?.label
      ?? config.value.scenario_preset_id,
    disturbance: config.value.disturbance_events.length === 0
      ? '无扰动'
      : config.value.disturbance_events.length === 1
        ? DISTURBANCE_EVENT_OPTIONS.find((item) => item.value === config.value.disturbance_events[0].preset_id)?.label
          ?? config.value.disturbance_events[0].event_type
        : `${DISTURBANCE_EVENT_OPTIONS.find((item) => item.value === config.value.disturbance_events[0].preset_id)?.label ?? '扰动事件'}等${config.value.disturbance_events.length}项`,
    flow: TRAFFIC_FLOW_MODE_OPTIONS.find((item) => item.value === config.value.flow_mode)?.label ?? config.value.flow_mode,
    time: `${config.value.simulation_start_time}-${config.value.simulation_end_time}`,
  }))

  const configNote = computed(() =>
    `当前配置：${labels.value.scenario} · ${labels.value.disturbance}\n${labels.value.flow} · ${labels.value.time}`,
  )

  function buildPayloadFor(candidate: CompactScenarioConfig): StartSimulationRequest {
    return buildSimulationPayload(
      candidate,
      intersection.value,
      periods.value,
      scenarioPresets.value,
      supportedIntersectionIds.value,
      controlModes.value,
    )
  }

  function buildPayload(): StartSimulationRequest {
    return buildPayloadFor(config.value)
  }

  function parseImportedConfig(input: unknown): CompactScenarioConfig {
    if (!isRecord(input)) throw new Error('配置文件格式无效')
    const candidate = isRecord(input.ui_config) ? input.ui_config : input
    const scenarioPresetId = candidate.scenario_preset_id ?? candidate.scenario_mode
    const flowMode = candidate.flow_mode
    const playbackSpeed = candidate.playback_speed ?? candidate.flow_scale
    const controlMode = candidate.control_mode

    if (
      typeof scenarioPresetId !== 'string'
      || !SCENARIO_MODE_OPTIONS.some((item) => item.value === scenarioPresetId)
    ) throw new Error('场景模式不受支持')
    if (!TRAFFIC_FLOW_MODE_OPTIONS.some((item) => item.value === flowMode)) throw new Error('交通流模式不受支持')
    const supportedControlModes = [...SUPPORTED_BACKEND_CONTROL_MODES]
    if (typeof controlMode === 'string' && !supportedControlModes.includes(controlMode as typeof supportedControlModes[number])) {
      throw new Error('管控算法不受支持')
    }
    const mode = flowMode as TrafficFlowMode
    const legacyTimes = legacyPresetTimes(candidate.time_preset)
    const range = SIMULATION_PERIOD_RANGES[mode]
    const startTime = typeof candidate.simulation_start_time === 'string'
      ? candidate.simulation_start_time
      : legacyTimes?.start ?? range.start
    let endTime = typeof candidate.simulation_end_time === 'string'
      ? candidate.simulation_end_time
      : legacyTimes?.end ?? maximumSimulationEndTime(mode, startTime)
    const startMinutes = clockTimeToMinutes(startTime)
    const endMinutes = clockTimeToMinutes(endTime)
    if (Number.isFinite(startMinutes) && Number.isFinite(endMinutes) && endMinutes - startMinutes > 15) {
      endTime = maximumSimulationEndTime(mode, startTime)
    }
    simulationTimeWindow(mode, startTime, endTime)

    const fallbackIntersectionIds = Array.isArray(candidate.disturbance_intersection_ids)
      ? candidate.disturbance_intersection_ids.filter((value): value is string => typeof value === 'string')
      : intersection.value?.intersection_id ? [intersection.value.intersection_id] : []
    let disturbanceEvents: CompactDisturbanceEvent[] = []
    if (Array.isArray(candidate.disturbance_events)) {
      disturbanceEvents = candidate.disturbance_events.map((value, index) => {
        if (!isRecord(value)) throw new Error('扰动事件格式无效')
        const preset = DISTURBANCE_EVENT_OPTIONS.find((item) => item.value === value.preset_id)
        if (!preset) throw new Error('扰动事件不受支持')
        const intersectionIds = Array.isArray(value.intersection_ids)
          ? value.intersection_ids.filter((id): id is string => typeof id === 'string')
          : []
        const importedTimes = resolveImportedDisturbanceTimes(value, startTime, endTime)
        const outerStart = clockTimeToMinutes(startTime)
        const outerEnd = clockTimeToMinutes(endTime)
        const importedStart = clockTimeToMinutes(importedTimes.startTime)
        const importedEnd = clockTimeToMinutes(importedTimes.endTime)
        const eventStartTime = Number.isFinite(importedStart) && importedStart >= outerStart && importedStart < outerEnd
          ? importedTimes.startTime
          : startTime
        const eventEndTime = Number.isFinite(importedEnd) && importedEnd > clockTimeToMinutes(eventStartTime) && importedEnd <= outerEnd
          ? importedTimes.endTime
          : endTime
        const isMajorEvent = preset.eventType === 'major_event_opening'
          || preset.eventType === 'major_event_closing'
        return {
          event_id: typeof value.event_id === 'string' ? value.event_id : `ui_import_${index + 1}`,
          preset_id: preset.value,
          event_type: preset.eventType,
          intersection_ids: intersectionIds,
          start_time: eventStartTime,
          end_time: eventEndTime,
          ...(isMajorEvent ? {
            vehicle_count: resolveMajorEventVehicleCount(value.vehicle_count),
          } : {}),
        }
      })
    } else if (typeof candidate.disturbance === 'string' && candidate.disturbance !== 'none') {
      const legacyPreset = DISTURBANCE_EVENT_OPTIONS.find((item) => item.eventType === candidate.disturbance)
      if (!legacyPreset) throw new Error('扰动事件不受支持')
      disturbanceEvents = [{
        event_id: 'ui_import_1',
        preset_id: legacyPreset.value,
        event_type: legacyPreset.eventType,
        intersection_ids: fallbackIntersectionIds,
        start_time: startTime,
        end_time: endTime,
        ...(
          legacyPreset.eventType === 'major_event_opening'
          || legacyPreset.eventType === 'major_event_closing'
            ? { vehicle_count: DEFAULT_MAJOR_EVENT_VEHICLE_COUNT }
            : {}
        ),
      }]
    }

    return {
      scenario_preset_id: scenarioPresetId,
      flow_mode: mode,
      disturbance_events: disturbanceEvents,
      simulation_start_time: startTime,
      simulation_end_time: endTime,
      playback_speed: typeof playbackSpeed === 'number' && playbackSpeeds.value.includes(playbackSpeed)
        ? playbackSpeed
        : 1,
      control_mode: typeof controlMode === 'string'
        ? controlMode
        : supportedControlModes.includes('fixed') ? 'fixed' : supportedControlModes[0],
    }
  }

  function applyImportedConfig(input: unknown): void {
    config.value = parseImportedConfig(input)
  }

  function buildExport(): ScenarioConfigExport {
    const backendRequest = buildSimulationPayload(
      config.value,
      null,
      periods.value,
      [],
      [],
      [...SUPPORTED_BACKEND_CONTROL_MODES],
    )
    return {
      version: SCENARIO_CONFIG_EXPORT_VERSION,
      exported_at: new Date().toISOString(),
      ui_config: { ...config.value },
      display: {
        scenario: labels.value.scenario,
        disturbance: labels.value.disturbance,
        flow_mode: labels.value.flow,
        simulation_time: labels.value.time,
        algorithm: config.value.control_mode,
      },
      backend_request: backendRequest,
      data_sources: {
        scenario: scenarioPresets.value.some((item) => item.preset_id === config.value.scenario_preset_id)
          ? 'catalog'
          : 'compatibility_preset',
        disturbance: 'catalog',
        time: 'local_range',
        algorithm: 'backend',
      },
    }
  }

  return {
    config,
    labels,
    configNote,
    activeTimeRange,
    activeMaximumEndTime,
    buildPayload,
    buildPayloadFor,
    parseImportedConfig,
    applyImportedConfig,
    buildExport,
  }
}
