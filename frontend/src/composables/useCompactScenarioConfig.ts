import { computed, ref, watch, type Ref } from 'vue'
import {
  DISTURBANCE_CHOICE_OPTIONS,
  SCENARIO_MODE_OPTIONS,
  SIMULATION_TIME_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
  type ScenarioModeId,
  type SimulationTimePresetId,
} from '../constants/scenarioOptions'
import {
  DASHBOARD_CONTROL_MODES,
  SIMULATION_SNAPSHOT_INTERVAL_MS,
  isBackendControlMode,
} from '../constants/simulationOptions'
import type { CatalogIntersection, CatalogScenarioPreset } from '../types/catalog'
import type { StartSimulationRequest } from '../types/simulation'
import type { DisturbanceType, TrafficFlowMode } from '../types/scenario'
import { requireSimulatableIntersection } from './catalogCapabilities'
import { buildStartSimulationRequest } from '../utils/scenarioPayload'

export interface CompactScenarioConfig {
  scenario_preset_id: ScenarioModeId
  flow_mode: TrafficFlowMode
  disturbance: DisturbanceType | 'none'
  time_preset: SimulationTimePresetId
  playback_speed: number
  control_mode: string
}

export interface ScenarioConfigExport {
  version: 2
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
    scenario_preset_id: 'xiongan_20',
    flow_mode: 'morning_peak',
    disturbance: 'lane_closure',
    time_preset: 'morning_0700',
    playback_speed: 1,
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

export function buildSimulationPayload(
  config: CompactScenarioConfig,
  intersection: CatalogIntersection | null,
  periods: string[],
): StartSimulationRequest {
  const simulatableIntersection = requireSimulatableIntersection(intersection)
  const time = resolveTimePreset(config)
  return buildStartSimulationRequest({
    scenarioPresetId: config.scenario_preset_id,
    period: resolvePeriod(config, periods),
    windowStartSeconds: time.windowStartSeconds,
    durationSeconds: time.durationSeconds,
    controlMode: isBackendControlMode(config.control_mode) ? config.control_mode : 'fixed',
    playbackSpeed: config.playback_speed,
    disturbance: config.disturbance,
    intersectionId: simulatableIntersection.intersection_id,
    snapshotIntervalSeconds: SIMULATION_SNAPSHOT_INTERVAL_MS / 1_000,
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function useCompactScenarioConfig(
  intersection: Ref<CatalogIntersection | null>,
  periods: Ref<string[]>,
  scenarioPresets: Ref<CatalogScenarioPreset[]>,
  playbackSpeeds: Ref<number[]>,
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

  watch(
    scenarioPresets,
    (presets) => {
      if (presets.length > 0 && !presets.some((item) => item.preset_id === config.value.scenario_preset_id)) {
        config.value.scenario_preset_id = presets[0].preset_id
      }
    },
    { immediate: true },
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

  const labels = computed(() => ({
    scenario: scenarioPresets.value.find((item) => item.preset_id === config.value.scenario_preset_id)?.label
      ?? SCENARIO_MODE_OPTIONS.find((item) => item.value === config.value.scenario_preset_id)?.label
      ?? config.value.scenario_preset_id,
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
    const scenarioPresetId = candidate.scenario_preset_id ?? candidate.scenario_mode
    const flowMode = candidate.flow_mode
    const disturbance = candidate.disturbance
    const timePreset = candidate.time_preset
    const playbackSpeed = candidate.playback_speed ?? candidate.flow_scale
    const controlMode = candidate.control_mode

    const supportedPresetIds = scenarioPresets.value.length > 0
      ? scenarioPresets.value.map((item) => item.preset_id)
      : SCENARIO_MODE_OPTIONS.map((item) => item.value)
    if (typeof scenarioPresetId !== 'string' || !supportedPresetIds.includes(scenarioPresetId)) throw new Error('场景模式不受支持')
    if (!TRAFFIC_FLOW_MODE_OPTIONS.some((item) => item.value === flowMode)) throw new Error('交通流模式不受支持')
    if (!DISTURBANCE_CHOICE_OPTIONS.some((item) => item.value === disturbance)) throw new Error('扰动事件不受支持')
    if (!SIMULATION_TIME_OPTIONS.some((item) => item.value === timePreset)) throw new Error('仿真时间不受支持')
    if (typeof controlMode === 'string' && !DASHBOARD_CONTROL_MODES.some((item) => item.value === controlMode)) throw new Error('管控算法不受支持')

    config.value = {
      scenario_preset_id: scenarioPresetId,
      flow_mode: flowMode as TrafficFlowMode,
      disturbance: disturbance as DisturbanceType | 'none',
      time_preset: timePreset as SimulationTimePresetId,
      playback_speed: typeof playbackSpeed === 'number' && playbackSpeeds.value.includes(playbackSpeed)
        ? playbackSpeed
        : 1,
      control_mode: typeof controlMode === 'string' ? controlMode : 'fixed',
    }
  }

  function buildExport(): ScenarioConfigExport {
    return {
      version: 2,
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
        scenario: 'catalog',
        disturbance: 'catalog',
        time: 'local_preset',
        algorithm: isBackendControlMode(config.value.control_mode) ? 'backend' : 'mock_preview',
      },
    }
  }

  return { config, labels, configNote, availableTimeOptions, buildPayload, applyImportedConfig, buildExport }
}
