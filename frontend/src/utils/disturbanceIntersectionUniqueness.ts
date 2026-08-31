import { formatIntersectionLabel } from './intersectionLabels.ts'

export interface IntersectionScopedDisturbanceEvent {
  event_id: string
  intersection_ids: string[]
}

export interface DisturbanceIntersectionConflict<T extends IntersectionScopedDisturbanceEvent> {
  intersectionId: string
  events: T[]
}

export function disturbanceIntersectionOwners<T extends IntersectionScopedDisturbanceEvent>(
  events: readonly T[],
  excludedEventId: string | null = null,
): Map<string, T> {
  const owners = new Map<string, T>()
  for (const event of events) {
    if (event.event_id === excludedEventId) continue
    for (const intersectionId of new Set(event.intersection_ids)) {
      if (!owners.has(intersectionId)) owners.set(intersectionId, event)
    }
  }
  return owners
}

export function disturbanceIntersectionConflicts<T extends IntersectionScopedDisturbanceEvent>(
  events: readonly T[],
): DisturbanceIntersectionConflict<T>[] {
  const eventsByIntersection = new Map<string, T[]>()
  for (const event of events) {
    for (const intersectionId of new Set(event.intersection_ids)) {
      const owners = eventsByIntersection.get(intersectionId) ?? []
      owners.push(event)
      eventsByIntersection.set(intersectionId, owners)
    }
  }
  return [...eventsByIntersection.entries()]
    .filter(([, owners]) => owners.length > 1)
    .map(([intersectionId, owners]) => ({ intersectionId, events: owners }))
    .sort((left, right) => left.intersectionId.localeCompare(
      right.intersectionId,
      undefined,
      { numeric: true },
    ))
}

export function assertUniqueDisturbanceIntersections<T extends IntersectionScopedDisturbanceEvent>(
  events: readonly T[],
  eventLabel: (event: T) => string = (event) => event.event_id,
): void {
  const conflicts = disturbanceIntersectionConflicts(events)
  if (conflicts.length === 0) return
  const details = conflicts.map((conflict) => (
    `${formatIntersectionLabel(conflict.intersectionId)}已用于${conflict.events
      .map((event) => `“${eventLabel(event)}”`)
      .join('和')}`
  ))
  throw new Error(`同一路口只能配置一个扰动事件：${details.join('；')}`)
}
