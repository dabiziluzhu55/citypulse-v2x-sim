import type {
  TrafficIntersection,
  TrafficLane,
  TrafficStateSnapshot,
  TrafficStateWsMessage,
  TrafficSummary,
  TrafficVehicle,
} from '../types/traffic'

function mergeById<T>(
  current: T[],
  incoming: T[],
  idKey: keyof T,
): T[] {
  if (incoming.length === 0) {
    return current
  }

  const map = new Map<string, T>()
  for (const item of current) {
    map.set(String(item[idKey]), item)
  }
  for (const item of incoming) {
    map.set(String(item[idKey]), item)
  }
  return Array.from(map.values())
}

export function mergeTrafficState(
  current: TrafficStateSnapshot | null,
  message: TrafficStateWsMessage,
  runId: string,
): TrafficStateSnapshot {
  const base: TrafficStateSnapshot = current ?? {
    run_id: runId,
    sim_time: message.timestamp,
    intersections: [],
    lanes: [],
    vehicles: [],
  }

  return {
    run_id: runId,
    sim_time: message.timestamp,
    intersections: mergeById<TrafficIntersection>(
      base.intersections,
      message.data.intersections ?? [],
      'intersection_id',
    ),
    lanes: mergeById<TrafficLane>(base.lanes, message.data.lanes ?? [], 'lane_id'),
    vehicles: mergeById<TrafficVehicle>(
      base.vehicles,
      message.data.vehicles ?? [],
      'vehicle_id',
    ),
  }
}

export function mergeTrafficSummary(
  current: TrafficSummary,
  message: TrafficStateWsMessage,
): TrafficSummary {
  return {
    vehicle_count: message.data.vehicle_count ?? current.vehicle_count,
    avg_speed: message.data.avg_speed ?? current.avg_speed,
  }
}
