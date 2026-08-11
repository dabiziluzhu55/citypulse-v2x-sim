import type { DisturbanceTargetPayload, StartSimulationRequest } from '../types/simulation'
import type { BackendControlMode } from '../constants/simulationOptions'
import type { DisturbanceType } from '../types/scenario'
import { resolveMajorEventVehicleCount } from './scenarioConfigMigration.ts'
import { assertUniqueDisturbanceIntersections } from './disturbanceIntersectionUniqueness.ts'
import {
  assertSafeLaneClosureEvents,
  safeLaneClosureLaneIds,
} from './safeLaneClosures.ts'

export interface ScenarioPayloadInput {
  scenarioPresetId: string
  period: string
  windowStartSeconds: number
  durationSeconds: number
  controlMode: BackendControlMode
  playbackSpeed: number
  disturbance?: DisturbanceType | 'none'
  intersectionId?: string
  disturbanceIntersectionIds?: string[]
  disturbanceEvents?: ScenarioDisturbanceInput[]
  eventId?: string
  now?: number
  snapshotIntervalSeconds: number
}

export interface ScenarioDisturbanceInput {
  eventId?: string
  eventType: DisturbanceType
  intersectionIds: string[]
  startSeconds?: number
  endSeconds?: number
  vehicleCount?: number
}

export function buildDisturbanceTargets(input: ScenarioPayloadInput): DisturbanceTargetPayload[] {
  const events = input.disturbanceEvents ?? (
    !input.disturbance || input.disturbance === 'none'
      ? []
      : [{
          eventId: input.eventId,
          eventType: input.disturbance,
          intersectionIds: input.disturbanceIntersectionIds?.length
            ? input.disturbanceIntersectionIds
            : input.intersectionId ? [input.intersectionId] : [],
        }]
  )
  assertUniqueDisturbanceIntersections(events.map((event, index) => ({
    event_id: event.eventId ?? `event_${index + 1}`,
    intersection_ids: event.intersectionIds,
    label: event.eventType,
  })), (event) => event.label)
  assertSafeLaneClosureEvents(events)
  return events.flatMap((event, eventIndex) => {
    const intersectionIds = [...new Set(event.intersectionIds)]
    if (intersectionIds.length === 0) {
      throw new Error('Select at least one disturbance intersection')
    }
    const eventRoot = event.eventId
      ?? `evt_${event.eventType}_${input.now ?? Date.now()}_${eventIndex + 1}`
    const start = event.startSeconds
      ?? Math.max(0, Math.min(60, input.durationSeconds - 1))
    const end = event.endSeconds ?? input.durationSeconds
    if (
      !Number.isFinite(start)
      || !Number.isFinite(end)
      || start < 0
      || start >= end
      || end > input.durationSeconds
    ) {
      throw new Error('Disturbance time must stay inside the simulation window')
    }
    return intersectionIds.map((intersectionId, intersectionIndex) => {
      const base = {
        intersection_id: intersectionId,
        event_id: intersectionIds.length === 1
          ? eventRoot
          : `${eventRoot}_${intersectionId}_${intersectionIndex + 1}`,
        start_seconds: start,
        end_seconds: end,
      }
      if (event.eventType === 'lane_closure') {
        return {
          event_type: 'lane_closure' as const,
          ...base,
          lane_ids: safeLaneClosureLaneIds(intersectionId),
        }
      }
      if (event.eventType === 'speed_limit') return { event_type: 'speed_limit' as const, ...base, max_speed: 5 }
      if (event.eventType === 'accident') return { event_type: 'accident' as const, ...base, position_ratio: 0.5 }
      const vehicleCount = resolveMajorEventVehicleCount(event.vehicleCount)
      if (event.eventType === 'major_event_opening') {
        return { event_type: 'major_event_opening' as const, ...base, vehicle_count: vehicleCount }
      }
      return { event_type: 'major_event_closing' as const, ...base, vehicle_count: vehicleCount }
    })
  })
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
    step_length: 0.1,
    realtime: true,
    gui: false,
    snapshot_interval_seconds: input.snapshotIntervalSeconds,
    disturbance_targets: buildDisturbanceTargets(input),
    playback_speed: input.playbackSpeed,
  }
}
