import { computed, ref, watch, type Ref } from 'vue'
import {
  DISTURBANCE_CHOICE_OPTIONS,
  SCENARIO_MODE_OPTIONS,
  SIMULATION_PERIOD_RANGES,
  SIMULATION_TIME_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
  simulationTimeWindow,
  type ScenarioModeId,
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
  disturbance_intersection_ids: string[]
  simulation_start_time: string
  simulation_end_time: string
  playback_speed: number
  control_mode: string
}

export interface ScenarioConfigExport {
  version: 3
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
    disturbance_intersection_ids: ['demo_2'],
    simulation_start_time: SIMULATION_PERIOD_RANGES.morning_peak.start,
    simulation_end_time: SIMULATION_PERIOD_RANGES.morning_peak.end,
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
  intersection: CatalogIntersection | null,
  periods: string[],
  scenarioPresets: CatalogScenarioPreset[] = [],
  supportedIntersectionIds: string[] = [],
): StartSimulationRequest {
  const simulatableIntersection = requireSimulatableIntersection(intersection)
  const time = simulationTimeWindow(
    config.flow_mode,
    config.simulation_start_time,
    config.simulation_end_time,
  )
  const preset = scenarioPresets.find((item) => item.preset_id === config.scenario_preset_id)
  const supported = new Set(supportedIntersectionIds.length
    ? supportedIntersectionIds
    : [simulatableIntersection.intersection_id])
  const disturbanceIntersectionIds = [...new Set(config.disturbance_intersection_ids)]
  if (config.disturbance !== 'none') {
    if (disturbanceIntersectionIds.length === 0) {
      throw new Error('请选择至少一个扰动路口')
    }
    const invalid = disturbanceIntersectionIds.filter((id) => (
      !supported.has(id) || (preset && !preset.intersection_ids.includes(id))
    ))
    if (invalid.length > 0) throw new Error(`扰动路口不可用于当前场景：${invalid.join(', ')}`)
  }
  return buildStartSimulationRequest({
    scenarioPresetId: config.scenario_preset_id,
    period: resolvePeriod(config, periods),
    windowStartSeconds: time.windowStartSeconds,
    durationSeconds: time.durationSeconds,
    controlMode: isBackendControlMode(config.control_mode) ? config.control_mode : 'fixed',
    playbackSpeed: config.playback_speed,
    disturbance: config.disturbance,
    disturbanceIntersectionIds,
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
) {
  const config = ref<CompactScenarioConfig>(defaultCompactConfig())
  const activeTimeRange = computed(() => SIMULATION_PERIOD_RANGES[config.value.flow_mode])

  watch(
    () => config.value.flow_mode,
    (mode) => {
      const range = SIMULATION_PERIOD_RANGES[mode]
      config.value.simulation_start_time = range.start
      config.value.simulation_end_time = range.end
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
    [() => config.value.scenario_preset_id, scenarioPresets, supportedIntersectionIds, intersection],
    ([presetId, presets, supportedIds, activeIntersection]) => {
      const preset = presets.find((item) => item.preset_id === presetId)
      if (!preset) return
      const supported = new Set(supportedIds)
      const available = preset.intersection_ids.filter((id) => supported.has(id))
      const retained = config.value.disturbance_intersection_ids.filter((id) => available.includes(id))
      if (retained.length > 0 || config.value.disturbance === 'none') {
        config.value.disturbance_intersection_ids = retained
        return
      }
      const activeId = activeIntersection?.intersection_id
      config.value.disturbance_intersection_ids = activeId && available.includes(activeId)
        ? [activeId]
        : available.slice(0, 1)
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
    const disturbance = candidate.disturbance
    const playbackSpeed = candidate.playback_speed ?? candidate.flow_scale
    const controlMode = candidate.control_mode

    const supportedPresetIds = scenarioPresets.value.length > 0
      ? scenarioPresets.value.map((item) => item.preset_id)
      : SCENARIO_MODE_OPTIONS.map((item) => item.value)
    if (typeof scenarioPresetId !== 'string' || !supportedPresetIds.includes(scenarioPresetId)) throw new Error('场景模式不受支持')
    if (!TRAFFIC_FLOW_MODE_OPTIONS.some((item) => item.value === flowMode)) throw new Error('交通流模式不受支持')
    if (!DISTURBANCE_CHOICE_OPTIONS.some((item) => item.value === disturbance)) throw new Error('扰动事件不受支持')
    if (typeof controlMode === 'string' && !DASHBOARD_CONTROL_MODES.some((item) => item.value === controlMode)) throw new Error('管控算法不受支持')

    const mode = flowMode as TrafficFlowMode
    const legacyTimes = legacyPresetTimes(candidate.time_preset)
    const range = SIMULATION_PERIOD_RANGES[mode]
    const startTime = typeof candidate.simulation_start_time === 'string'
      ? candidate.simulation_start_time
      : legacyTimes?.start ?? range.start
    const endTime = typeof candidate.simulation_end_time === 'string'
      ? candidate.simulation_end_time
      : legacyTimes?.end ?? range.end
    simulationTimeWindow(mode, startTime, endTime)

    const disturbanceIds = Array.isArray(candidate.disturbance_intersection_ids)
      ? candidate.disturbance_intersection_ids.filter((value): value is string => typeof value === 'string')
      : intersection.value?.intersection_id ? [intersection.value.intersection_id] : []

    return {
      scenario_preset_id: scenarioPresetId,
      flow_mode: mode,
      disturbance: disturbance as DisturbanceType | 'none',
      disturbance_intersection_ids: disturbanceIds,
      simulation_start_time: startTime,
      simulation_end_time: endTime,
      playback_speed: typeof playbackSpeed === 'number' && playbackSpeeds.value.includes(playbackSpeed)
        ? playbackSpeed
        : 1,
      control_mode: typeof controlMode === 'string' ? controlMode : 'fixed',
    }
  }

  function applyImportedConfig(input: unknown): void {
    config.value = parseImportedConfig(input)
  }

  function buildExport(): ScenarioConfigExport {
    return {
      version: 3,
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
        time: 'local_range',
        algorithm: isBackendControlMode(config.value.control_mode) ? 'backend' : 'mock_preview',
      },
    }
  }

  return {
    config,
    labels,
    configNote,
    activeTimeRange,
    buildPayload,
    buildPayloadFor,
    parseImportedConfig,
    applyImportedConfig,
    buildExport,
  }
}
