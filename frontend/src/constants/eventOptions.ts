import type { EventLevel, EventType } from '../types/events'

export const EVENT_REFRESH_INTERVAL_MS = 30_000

export const EVENT_TYPE_LABELS: Record<string, string> = {
  congestion: '拥堵',
  abnormal_parking: '异常停车',
  lane_closure: '施工占道',
  queue_spillover: '排队溢出',
}

export const EVENT_LEVEL_LABELS: Record<EventLevel, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
}

export const EVENT_LEVEL_CLASS: Record<EventLevel, string> = {
  low: 'cyan',
  medium: 'yellow',
  high: 'red',
}

export function formatEventType(type: EventType): string {
  return EVENT_TYPE_LABELS[type] ?? type
}

export function formatEventLevel(level: EventLevel): string {
  return EVENT_LEVEL_LABELS[level] ?? level
}

export function formatLocation(location: {
  intersection_id?: string
  lane_id?: string
  edge_id?: string
}): string {
  const parts = [location.intersection_id, location.lane_id, location.edge_id].filter(Boolean)
  return parts.length > 0 ? parts.join(' / ') : '--'
}

export function formatSuggestion(suggestion: string | undefined): string {
  if (!suggestion) {
    return '--'
  }

  const labels: Record<string, string> = {
    'extend north-south green phase': '延长南北向绿灯',
    'extend green': '延长绿灯',
    detour: '绕行',
    speed_limit: '限速',
    diversion: '分流',
  }

  return labels[suggestion] ?? suggestion
}

export function inferEventCause(evidence?: {
  avg_speed?: number
  queue_length?: number
  avg_waiting_time?: number
}): string {
  if (!evidence) {
    return '--'
  }

  const causes: string[] = []
  if (evidence.avg_speed != null && evidence.avg_speed < 3) {
    causes.push('速度低')
  }
  if (evidence.queue_length != null && evidence.queue_length >= 20) {
    causes.push('排队长')
  }
  if (evidence.avg_waiting_time != null && evidence.avg_waiting_time >= 45) {
    causes.push('等待时间长')
  }

  return causes.length > 0 ? causes.join('、') : '综合指标异常'
}
