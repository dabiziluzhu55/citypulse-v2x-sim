import type {
  CloudAdvice,
  CollaborationStateSnapshot,
  CollaborationStateWsMessage,
  CollaborationVehicle,
  EdgeAgent,
} from '../types/collaboration'

function mergeById<T>(current: T[], incoming: T[], idKey: keyof T): T[] {
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

function mergeCloud(current: CloudAdvice, incoming?: Partial<CloudAdvice>): CloudAdvice {
  if (!incoming) {
    return current
  }
  return { ...current, ...incoming }
}

export function mergeCollaborationState(
  current: CollaborationStateSnapshot | null,
  message: CollaborationStateWsMessage,
  runId: string,
): CollaborationStateSnapshot {
  const base: CollaborationStateSnapshot = current ?? {
    run_id: runId,
    sim_time: message.timestamp,
    cloud: {
      strategy: '',
      target_area: '',
      reason: '',
      algorithm: '',
    },
    edges: [],
    vehicles: [],
  }

  return {
    run_id: runId,
    sim_time: message.timestamp,
    cloud: mergeCloud(base.cloud, message.data.cloud),
    edges: mergeById<EdgeAgent>(base.edges, message.data.edges ?? [], 'edge_agent_id'),
    vehicles: mergeById<CollaborationVehicle>(
      base.vehicles,
      message.data.vehicles ?? [],
      'vehicle_id',
    ),
  }
}
