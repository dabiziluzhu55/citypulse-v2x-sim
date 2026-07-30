import type { DisturbanceTargetPayload, StartSimulationRequest } from '../types/simulation'

export interface ScenarioPayloadInput {
  scenarioPresetId: string
  period: string
  windowStartSeconds: number
  durationSeconds: number
  controlMode: string
  playbackSpeed: number
  disturbance: 'lane_closure' | 'speed_limit' | 'accident' | 'none'
  intersectionId: string
  eventId?: string
  now?: number
  snapshotIntervalSeconds: number
}

export function buildDisturbanceTargets(input: ScenarioPayloadInput): DisturbanceTargetPayload[] {
  if (input.disturbance === 'none') return []
  const start = Math.max(0, Math.min(60, input.durationSeconds - 1))
  const base = {
    intersection_id: input.intersectionId,
    event_id: input.eventId ?? `evt_${input.disturbance}_${input.now ?? Date.now()}`,
    start_seconds: start,
    end_seconds: input.durationSeconds,
  }
  if (input.disturbance === 'lane_closure') return [{ event_type: 'lane_closure', ...base }]
  if (input.disturbance === 'speed_limit') {
    return [{ event_type: 'speed_limit', ...base, max_speed: 5 }]
  }
  return [{ event_type: 'accident', ...base, position_ratio: 0.5 }]
}

export function buildStartSimulationRequest(input: ScenarioPayloadInput): StartSimulationRequest {
  return {
    scenario_preset_id: input.scenarioPresetId,
    period: input.period,
    origins: {},
    window_start_seconds: input.windowStartSeconds,
    duration_seconds: input.durationSeconds,
    control_mode: input.controlMode,
    seed: 42,
    step_length: 0.05,
    realtime: true,
    gui: false,
    snapshot_interval_seconds: input.snapshotIntervalSeconds,
    disturbance_targets: buildDisturbanceTargets(input),
    playback_speed: input.playbackSpeed,
  }
}
