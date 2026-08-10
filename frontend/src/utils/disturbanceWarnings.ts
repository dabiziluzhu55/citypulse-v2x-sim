import { clockTimeToMinutes } from '../constants/scenarioOptions.ts'
import type { ScenarioDraftDisturbanceEvent } from '../composables/useScenarioDraftStore'
import type { IntersectionTopologyNode } from '../mapv/intersectionTopology'

export type DisturbanceWarningStatus = 'configured' | 'active' | 'completed'

export interface DisturbanceWarningAggregate {
  intersectionId: string
  longitude: number
  latitude: number
  events: ScenarioDraftDisturbanceEvent[]
  status: DisturbanceWarningStatus
}

type DisturbanceWarningEventInput = Readonly<
  Omit<ScenarioDraftDisturbanceEvent, 'intersection_ids'>
> & { readonly intersection_ids: readonly string[] }

function eventOffsetSeconds(clockTime: string, simulationStartTime: string): number {
  return (clockTimeToMinutes(clockTime) - clockTimeToMinutes(simulationStartTime)) * 60
}

function aggregateStatus(
  events: ScenarioDraftDisturbanceEvent[],
  simulationStartTime: string,
  elapsedSeconds?: number,
): DisturbanceWarningStatus {
  if (!Number.isFinite(elapsedSeconds)) return 'configured'
  if (events.some((event) => {
    const start = eventOffsetSeconds(event.start_time, simulationStartTime)
    const end = eventOffsetSeconds(event.end_time, simulationStartTime)
    return elapsedSeconds! >= start && elapsedSeconds! < end
  })) return 'active'
  if (events.every((event) => (
    elapsedSeconds! >= eventOffsetSeconds(event.end_time, simulationStartTime)
  ))) return 'completed'
  return 'configured'
}

export function buildDisturbanceWarningAggregates(
  nodes: IntersectionTopologyNode[],
  events: readonly DisturbanceWarningEventInput[],
  simulationStartTime: string,
  elapsedSeconds?: number,
): DisturbanceWarningAggregate[] {
  const byIntersection = new Map<string, ScenarioDraftDisturbanceEvent[]>()
  for (const event of events) {
    const mutableEvent: ScenarioDraftDisturbanceEvent = {
      ...event,
      intersection_ids: [...event.intersection_ids],
    }
    for (const intersectionId of new Set(event.intersection_ids)) {
      byIntersection.set(intersectionId, [
        ...(byIntersection.get(intersectionId) ?? []),
        mutableEvent,
      ])
    }
  }
  return nodes.flatMap((node) => {
    const intersectionEvents = byIntersection.get(node.intersectionId)
    if (!intersectionEvents?.length) return []
    return [{
      intersectionId: node.intersectionId,
      longitude: node.longitude,
      latitude: node.latitude,
      events: intersectionEvents,
      status: aggregateStatus(intersectionEvents, simulationStartTime, elapsedSeconds),
    }]
  })
}
