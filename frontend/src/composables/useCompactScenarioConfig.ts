import { computed, ref, watch, type Ref } from 'vue'
import {
  DISTURBANCE_CHOICE_OPTIONS,
  OD_PRESET_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
  type OdPresetId,
} from '../constants/scenarioOptions'
import { DEFAULT_INTERSECTION_ID } from '../constants/simulationOptions'
import type { CatalogIntersection } from '../types/catalog'
import type {
  DisturbanceEventPayload,
  StartSimulationRequest,
} from '../types/simulation'
import type { DisturbanceType, TrafficFlowMode } from '../types/scenario'

export interface CompactScenarioConfig {
  template_id: string
  flow_mode: TrafficFlowMode
  od_preset_id: OdPresetId
  disturbance: DisturbanceType | 'none'
  flow_scale: number
  duration: number
  control_mode: string
}

const FLOW_MODE_TO_PERIOD: Record<TrafficFlowMode, string> = {
  flat: 'off_peak',
  morning_peak: 'morning_peak',
  evening_peak: 'evening_peak',
}

function defaultCompactConfig(): CompactScenarioConfig {
  return {
    template_id: DEFAULT_INTERSECTION_ID,
    flow_mode: 'morning_peak',
    od_preset_id: 'main_school',
    disturbance: 'none',
    flow_scale: 1.2,
    duration: 600,
    control_mode: 'fixed',
  }
}

function resolvePeriod(config: CompactScenarioConfig, periods: string[]): string {
  const mapped = FLOW_MODE_TO_PERIOD[config.flow_mode]
  if (periods.includes(mapped)) {
    return mapped
  }
  return periods[0] ?? mapped
}

function buildInitialEvents(
  config: CompactScenarioConfig,
  intersection: CatalogIntersection | null,
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

  const start = Math.max(0, Math.min(60, config.duration - 1))
  const end = config.duration
  const eventId = `evt_${config.disturbance}_${Date.now()}`

  if (config.disturbance === 'lane_closure') {
    return [
      {
        event_type: 'lane_closure',
        event_id: eventId,
        start_seconds: start,
        end_seconds: end,
        lane_ids: [incomingLanes[0]],
      },
    ]
  }

  if (config.disturbance === 'speed_limit') {
    return [
      {
        event_type: 'speed_limit',
        event_id: eventId,
        start_seconds: start,
        end_seconds: end,
        lane_ids: [incomingLanes[0]],
        max_speed: 5,
      },
    ]
  }

  return [
    {
      event_type: 'accident',
      event_id: eventId,
      start_seconds: start,
      end_seconds: end,
      lane_id: incomingLanes[0],
      position_ratio: 0.5,
    },
  ]
}

export function buildSimulationPayload(
  config: CompactScenarioConfig,
  intersection: CatalogIntersection | null,
  periods: string[],
): StartSimulationRequest {
  return {
    intersection_ids: [DEFAULT_INTERSECTION_ID],
    period: resolvePeriod(config, periods),
    origins: {},
    window_start_seconds: 0,
    duration_seconds: config.duration,
    flow_multiplier: config.flow_scale,
    control_mode: config.control_mode || 'fixed',
    seed: 42,
    step_length: 0.05,
    realtime: true,
    gui: false,
    snapshot_interval_seconds: 0.2,
    initial_events: buildInitialEvents(config, intersection),
  }
}

export function useCompactScenarioConfig(
  intersection: Ref<CatalogIntersection | null>,
  periods: Ref<string[]>,
) {
  const config = ref<CompactScenarioConfig>(defaultCompactConfig())

  watch(
    periods,
    (items) => {
      if (items.length === 0) {
        return
      }
      const mapped = FLOW_MODE_TO_PERIOD[config.value.flow_mode]
      if (!items.includes(mapped)) {
        const inverse = (Object.entries(FLOW_MODE_TO_PERIOD).find(
          ([, period]) => period === items[0],
        )?.[0] ?? 'morning_peak') as TrafficFlowMode
        config.value.flow_mode = inverse
      }
    },
    { immediate: true },
  )

  const configNote = computed(() => {
    const flowLabel =
      TRAFFIC_FLOW_MODE_OPTIONS.find((item) => item.value === config.value.flow_mode)?.label ??
      config.value.flow_mode
    const odLabel =
      OD_PRESET_OPTIONS.find((item) => item.id === config.value.od_preset_id)?.label ??
      config.value.od_preset_id
    const disturbanceLabel =
      DISTURBANCE_CHOICE_OPTIONS.find((item) => item.value === config.value.disturbance)?.label ??
      config.value.disturbance
    const durationMin = Math.round(config.value.duration / 60)
    return `场景：demo_2 单路口 | 模式：${flowLabel} | OD：${odLabel} | 倍率：${config.value.flow_scale}x | 时长：${durationMin}min | 扰动：${disturbanceLabel}`
  })

  function buildPayload(): StartSimulationRequest {
    return buildSimulationPayload(config.value, intersection.value, periods.value)
  }

  return {
    config,
    configNote,
    buildPayload,
  }
}
