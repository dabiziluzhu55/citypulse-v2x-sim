import { computed, ref, watch, type Ref } from 'vue'
import {
  DISTURBANCE_CHOICE_OPTIONS,
  SCENARIO_MODE_OPTIONS,
  SIMULATION_TIME_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
  type ScenarioModeId,
  type SimulationTimePresetId,
} from '../constants/scenarioOptions'
import { DASHBOARD_CONTROL_MODES, DEFAULT_INTERSECTION_ID, isBackendControlMode } from '../constants/simulationOptions'
import type { CatalogIntersection } from '../types/catalog'
import type { DisturbanceEventPayload, StartSimulationRequest } from '../types/simulation'
import type { DisturbanceType, TrafficFlowMode } from '../types/scenario'

export interface CompactScenarioConfig {
  scenario_mode: ScenarioModeId
  flow_mode: TrafficFlowMode
  disturbance: DisturbanceType | 'none'
  time_preset: SimulationTimePresetId
  flow_scale: number
  control_mode: string
}

export interface ScenarioConfigExport {
  version: 1
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
    time: 'local_preset'
    algorithm: 'backend' | 'mock_preview'
  }
}

const FLOW_MODE_TO_PERIOD: Record<TrafficFlowMode, string> = {
  flat: 'off_peak',
  morning_peak: 'morning_peak',
  evening_peak: 'evening_peak',
}

function defaultCompactConfig(): CompactScenarioConfig {
  return {
    scenario_mode: 'xiongan_20',
    flow_mode: 'morning_peak',
    disturbance: 'lane_closure',
    time_preset: 'morning_15',
    flow_scale: 1,
    control_mode: 'fixed',
  }
}

function resolvePeriod(config: CompactScenarioConfig, periods: string[]): string {
  const mapped = FLOW_MODE_TO_PERIOD[config.flow_mode]
  return periods.includes(mapped) ? mapped : periods[0] ?? mapped
}

function resolveTimePreset(config: CompactScenarioConfig) {
  return (
    SIMULATION_TIME_OPTIONS.find((item) => item.value === config.time_preset) ??
    SIMULATION_TIME_OPTIONS.find((item) => item.flowMode === config.flow_mode) ??
    SIMULATION_TIME_OPTIONS[0]
  )
}

function buildInitialEvents(
  config: CompactScenarioConfig,
  intersection: CatalogIntersection | null,
  durationSeconds: number,
): DisturbanceEventPayload[] {
  if (config.disturbance === 'none' || !intersection) {
    return []
  }

  const incomingLanes = intersection.lanes
    .filter((lane) => lane.role === 'incoming')
    .map((lane) => lane.lane_id)
  if (incomingLanes.length === 0) {
    return []
  }

  const start = Math.max(0, Math.min(60, durationSeconds - 1))
  const eventId = `evt_${config.disturbance}_${Date.now()}`

  if (config.disturbance === 'lane_closure') {
    return [{ event_type: 'lane_closure', event_id: eventId, start_seconds: start, end_seconds: durationSeconds, lane_ids: [incomingLanes[0]] }]
  }
  if (config.disturbance === 'speed_limit') {
    return [{ event_type: 'speed_limit', event_id: eventId, start_seconds: start, end_seconds: durationSeconds, lane_ids: [incomingLanes[0]], max_speed: 5 }]
  }
  return [{ event_type: 'accident', event_id: eventId, start_seconds: start, end_seconds: durationSeconds, lane_id: incomingLanes[0], position_ratio: 0.5 }]
}

export function buildSimulationPayload(
  config: CompactScenarioConfig,
  intersection: CatalogIntersection | null,
  periods: string[],
): StartSimulationRequest {
  const time = resolveTimePreset(config)
  const scenario = SCENARIO_MODE_OPTIONS.find((item) => item.value === config.scenario_mode)
  return {
    intersection_ids: [scenario?.backendIntersectionId ?? DEFAULT_INTERSECTION_ID],
    period: resolvePeriod(config, periods),
    origins: {},
    window_start_seconds: time.windowStartSeconds,
    duration_seconds: time.durationSeconds,
    flow_multiplier: config.flow_scale,
    control_mode: isBackendControlMode(config.control_mode) ? config.control_mode : 'fixed',
    seed: 42,
    step_length: 0.05,
    realtime: true,
    gui: false,
    snapshot_interval_seconds: 0.2,
    initial_events: buildInitialEvents(config, intersection, time.durationSeconds),
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function useCompactScenarioConfig(
  intersection: Ref<CatalogIntersection | null>,
  periods: Ref<string[]>,
) {
  const config = ref<CompactScenarioConfig>(defaultCompactConfig())

  const availableTimeOptions = computed(() =>
    SIMULATION_TIME_OPTIONS.filter((item) => item.flowMode === config.value.flow_mode),
  )

  watch(
    () => config.value.flow_mode,
    (mode) => {
      const current = SIMULATION_TIME_OPTIONS.find((item) => item.value === config.value.time_preset)
      if (current?.flowMode !== mode) {
        const fallback = SIMULATION_TIME_OPTIONS.find((item) => item.flowMode === mode)
        if (fallback) config.value.time_preset = fallback.value
      }
    },
  )

  const labels = computed(() => ({
    scenario: SCENARIO_MODE_OPTIONS.find((item) => item.value === config.value.scenario_mode)?.label ?? config.value.scenario_mode,
    disturbance: DISTURBANCE_CHOICE_OPTIONS.find((item) => item.value === config.value.disturbance)?.label ?? config.value.disturbance,
    flow: TRAFFIC_FLOW_MODE_OPTIONS.find((item) => item.value === config.value.flow_mode)?.label ?? config.value.flow_mode,
    time: resolveTimePreset(config.value).label,
  }))

  const configNote = computed(() =>
    `当前配置：${labels.value.scenario}｜${labels.value.disturbance}\n｜${labels.value.flow}｜${labels.value.time}`,
  )

  function buildPayload(): StartSimulationRequest {
    return buildSimulationPayload(config.value, intersection.value, periods.value)
  }

  function applyImportedConfig(input: unknown): void {
    if (!isRecord(input)) throw new Error('配置文件格式无效')
    const candidate = isRecord(input.ui_config) ? input.ui_config : input
    const scenarioMode = candidate.scenario_mode
    const flowMode = candidate.flow_mode
    const disturbance = candidate.disturbance
    const timePreset = candidate.time_preset
    const flowScale = candidate.flow_scale
    const controlMode = candidate.control_mode

    if (!SCENARIO_MODE_OPTIONS.some((item) => item.value === scenarioMode)) throw new Error('场景模式不受支持')
    if (!TRAFFIC_FLOW_MODE_OPTIONS.some((item) => item.value === flowMode)) throw new Error('交通流模式不受支持')
    if (!DISTURBANCE_CHOICE_OPTIONS.some((item) => item.value === disturbance)) throw new Error('扰动事件不受支持')
    if (!SIMULATION_TIME_OPTIONS.some((item) => item.value === timePreset)) throw new Error('仿真时间不受支持')
    if (typeof controlMode === 'string' && !DASHBOARD_CONTROL_MODES.some((item) => item.value === controlMode)) throw new Error('管控算法不受支持')

    config.value = {
      scenario_mode: scenarioMode as ScenarioModeId,
      flow_mode: flowMode as TrafficFlowMode,
      disturbance: disturbance as DisturbanceType | 'none',
      time_preset: timePreset as SimulationTimePresetId,
      flow_scale: typeof flowScale === 'number' && flowScale >= 0.1 && flowScale <= 5 ? flowScale : 1,
      control_mode: typeof controlMode === 'string' ? controlMode : 'fixed',
    }
  }

  function buildExport(): ScenarioConfigExport {
    return {
      version: 1,
      exported_at: new Date().toISOString(),
      ui_config: { ...config.value },
      display: {
        scenario: labels.value.scenario,
        disturbance: labels.value.disturbance,
        flow_mode: labels.value.flow,
        simulation_time: labels.value.time,
        algorithm: config.value.control_mode,
      },
      backend_request: buildPayload(),
      data_sources: {
        scenario: 'compatibility_preset',
        disturbance: 'catalog',
        time: 'local_preset',
        algorithm: isBackendControlMode(config.value.control_mode) ? 'backend' : 'mock_preview',
      },
    }
  }

  return { config, labels, configNote, availableTimeOptions, buildPayload, applyImportedConfig, buildExport }
}
