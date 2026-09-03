import type { EventDetectedWsMessage, TrafficEvent } from '../types/events'
import { formatIntersectionLabel } from './intersectionLabels'

export function mergeDetectedEvent(
  events: TrafficEvent[],
  message: EventDetectedWsMessage,
): TrafficEvent[] {
  const incoming = message.data
  const merged: TrafficEvent = {
    event_id: incoming.event_id,
    time: incoming.time ?? message.timestamp,
    type: incoming.type,
    level: incoming.level,
    location: incoming.location,
    description: incoming.description ?? `在${formatIntersectionLabel(
      incoming.location.intersection_id ?? '未知路口',
    )}识别到${incoming.type}`,
    evidence: incoming.evidence,
    suggestion: incoming.suggestion,
  }

  const map = new Map<string, TrafficEvent>()
  for (const event of events) {
    map.set(event.event_id, event)
  }
  map.set(merged.event_id, { ...(map.get(merged.event_id) ?? {}), ...merged })

  return Array.from(map.values()).sort((a, b) => b.time - a.time)
}
