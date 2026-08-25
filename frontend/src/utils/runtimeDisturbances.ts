import type {
  DisturbanceTargetPayload,
  SimulationEvent,
  SimulationSnapshot,
} from '../types/simulation'

export type DisturbanceRuntimeState =
  | 'SCHEDULED'
  | 'ACTIVE'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export interface DisturbanceRuntimeTarget {
  sessionId: string
  eventId: string
  intersectionId: string
  eventType: DisturbanceTargetPayload['event_type']
  startSeconds: number
  endSeconds: number
  parameters: Record<string, unknown>
  source?: 'configured' | 'snapshot'
}

export interface DisturbanceRuntimeView extends DisturbanceRuntimeTarget {
  state: DisturbanceRuntimeState
  error: string | null
  details: Record<string, unknown>
}

const DISTURBANCE_EVENT_TYPES = new Set<DisturbanceRuntimeTarget['eventType']>([
  'lane_closure',
  'speed_limit',
  'accident',
  'major_event_opening',
  'major_event_closing',
])

export interface StoredDisturbanceRuntimeTargets {
  version: 1
  sessionId: string
  targets: DisturbanceRuntimeTarget[]
}

const RUNTIME_STATES = new Set<DisturbanceRuntimeState>([
  'SCHEDULED',
  'ACTIVE',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
])

export const DISTURBANCE_RUNTIME_STORAGE_KEY = 'citypulse.active-disturbance-runtime.v1'

export function freezeDisturbanceRuntimeTargets(
  sessionId: string,
  targets: readonly DisturbanceTargetPayload[],
): DisturbanceRuntimeTarget[] {
  return targets.map((target, index) => {
    const {
      event_id: eventId,
      intersection_id: intersectionId,
      event_type: eventType,
      start_seconds: startSeconds,
      end_seconds: endSeconds,
      ...parameters
    } = target
    return {
      sessionId,
      eventId: eventId || `event_${index + 1}`,
      intersectionId,
      eventType,
      startSeconds,
      endSeconds,
      parameters: structuredClone(parameters),
      source: 'configured',
    }
  })
}

export function runtimeDisturbanceViews(
  targets: readonly DisturbanceRuntimeTarget[],
  snapshot: SimulationSnapshot | null,
): DisturbanceRuntimeView[] {
  const events = new Map((snapshot?.events ?? []).map((event) => [event.event_id, event] as const))
  const configuredTargets = [...new Map(targets.map((target) => [target.eventId, target] as const)).values()]
  const configured = configuredTargets.map((target) => {
    const event = events.get(target.eventId)
    const rawState = event?.state
    const state: DisturbanceRuntimeState = rawState && RUNTIME_STATES.has(rawState)
      ? rawState
      : 'SCHEDULED'
    return {
      ...target,
      state,
      error: typeof event?.error === 'string' && event.error.trim() ? event.error : null,
      details: runtimeEventDetails(event, target.parameters),
    }
  })
  const configuredIds = new Set(configured.map((event) => event.eventId))
  const recovered = (snapshot?.events ?? []).flatMap((event): DisturbanceRuntimeView[] => {
    if (configuredIds.has(event.event_id) || !DISTURBANCE_EVENT_TYPES.has(
      event.event_type as DisturbanceRuntimeTarget['eventType'],
    )) return []
    const details = runtimeEventDetails(event, {})
    const intersectionValue = event.intersection_id ?? details.intersection_id
    const state = event.state && RUNTIME_STATES.has(event.state) ? event.state : 'SCHEDULED'
    return [{
      sessionId: snapshot?.session_id ?? '',
      eventId: event.event_id,
      intersectionId: typeof intersectionValue === 'string' ? intersectionValue : '',
      eventType: event.event_type as DisturbanceRuntimeTarget['eventType'],
      startSeconds: Number.isFinite(event.start_seconds) ? Number(event.start_seconds) : 0,
      endSeconds: Number.isFinite(event.end_seconds)
        ? Number(event.end_seconds)
        : Number(snapshot?.duration_seconds) || 0,
      parameters: {},
      source: 'snapshot',
      state,
      error: typeof event.error === 'string' && event.error.trim() ? event.error : null,
      details,
    }]
  })
  return [...configured, ...recovered]
}

export function runtimeDisturbanceHasSceneMarker(view: DisturbanceRuntimeView): boolean {
  return view.state !== 'CANCELLED'
    && (view.eventType === 'accident' || view.eventType.startsWith('major_event_'))
}

function runtimeEventDetails(
  event: SimulationEvent | undefined,
  targetParameters: Record<string, unknown>,
): Record<string, unknown> {
  const details = event?.details && typeof event.details === 'object' ? event.details : {}
  const topLevel = event ? Object.fromEntries(Object.entries(event).filter(([key]) => (
    !['event_id', 'event_type', 'state', 'start_seconds', 'end_seconds', 'error', 'details'].includes(key)
  ))) : {}
  return { ...targetParameters, ...topLevel, ...details }
}

export function disturbanceRuntimeTypeLabel(eventType: string): string {
  return ({
    lane_closure: '施工占道',
    speed_limit: '道路限速',
    accident: '交通事故',
    major_event_opening: '大型活动开场',
    major_event_closing: '大型活动散场',
  } as Record<string, string>)[eventType] ?? eventType
}

export function disturbanceRuntimeStateLabel(state: DisturbanceRuntimeState): string {
  return ({
    SCHEDULED: '计划中',
    ACTIVE: '生效中',
    COMPLETED: '已完成',
    FAILED: '执行失败',
    CANCELLED: '已取消',
  } as const)[state]
}

export function runtimeDisturbanceLaneIds(view: DisturbanceRuntimeView): string[] {
  const values = [
    view.details.lane_ids,
    view.details.source_lane_ids,
    view.details.destination_lane_ids,
    view.details.lane_id,
    view.details.venue_lane_id,
  ]
  return [...new Set(values.flatMap((value) => (
    Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string')
      : typeof value === 'string' ? [value] : []
  )))]
}

export function parseStoredDisturbanceRuntimeTargets(value: string | null): StoredDisturbanceRuntimeTargets | null {
  if (!value) return null
  try {
    const parsed = JSON.parse(value) as Partial<StoredDisturbanceRuntimeTargets>
    if (parsed.version !== 1 || typeof parsed.sessionId !== 'string' || !Array.isArray(parsed.targets)) {
      return null
    }
    const valid = parsed.targets.every((target) => (
      target
      && target.sessionId === parsed.sessionId
      && typeof target.eventId === 'string'
      && typeof target.intersectionId === 'string'
      && typeof target.eventType === 'string'
      && Number.isFinite(target.startSeconds)
      && Number.isFinite(target.endSeconds)
      && target.parameters
      && typeof target.parameters === 'object'
    ))
    return valid ? parsed as StoredDisturbanceRuntimeTargets : null
  } catch {
    return null
  }
}
