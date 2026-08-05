import { readonly, ref } from 'vue'
import type { DisturbancePresetId } from '../constants/scenarioOptions'
import type { DisturbanceType } from '../types/scenario'

export interface ScenarioDraftDisturbanceEvent {
  event_id: string
  preset_id: DisturbancePresetId
  event_type: DisturbanceType
  intersection_ids: string[]
  start_time: string
  end_time: string
  vehicle_count?: number
}

const disturbanceEvents = ref<ScenarioDraftDisturbanceEvent[]>([])
const simulationStartTime = ref('07:00')
const simulationEndTime = ref('09:00')

export function publishScenarioDraft(input: {
  disturbanceEvents: ScenarioDraftDisturbanceEvent[]
  simulationStartTime: string
  simulationEndTime: string
}): void {
  disturbanceEvents.value = input.disturbanceEvents.map((event) => ({
    ...event,
    intersection_ids: [...event.intersection_ids],
  }))
  simulationStartTime.value = input.simulationStartTime
  simulationEndTime.value = input.simulationEndTime
}

export function useScenarioDraftStore() {
  return {
    disturbanceEvents: readonly(disturbanceEvents),
    simulationStartTime: readonly(simulationStartTime),
    simulationEndTime: readonly(simulationEndTime),
  }
}
