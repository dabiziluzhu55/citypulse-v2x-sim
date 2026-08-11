import safeLaneClosureCatalog from '../assets/safe-lane-closures.json' with { type: 'json' }
import { formatIntersectionLabel } from './intersectionLabels.ts'

interface LaneClosureCatalogEntry {
  selectedLaneIds: string[]
  unavailableReason: string | null
}

interface LaneClosureEventLike {
  event_type?: string
  eventType?: string
  intersection_ids?: string[]
  intersectionIds?: string[]
}

const intersections = safeLaneClosureCatalog.intersections as Record<string, LaneClosureCatalogEntry>

export interface LaneClosureAvailability {
  available: boolean
  laneIds: string[]
  reason: string | null
}

export const SAFE_LANE_CLOSURE_SOURCE_SHA256 = safeLaneClosureCatalog.sourceSha256

export function laneClosureAvailability(intersectionId: string): LaneClosureAvailability {
  const entry = intersections[intersectionId]
  if (!entry) {
    return {
      available: false,
      laneIds: [],
      reason: '安全封闭车道目录中不存在该路口',
    }
  }
  return {
    available: entry.selectedLaneIds.length > 0,
    laneIds: [...entry.selectedLaneIds],
    reason: entry.unavailableReason,
  }
}

export function safeLaneClosureLaneIds(intersectionId: string): string[] {
  const availability = laneClosureAvailability(intersectionId)
  if (!availability.available) {
    throw new Error(
      `${formatIntersectionLabel(intersectionId)}不支持施工占道：${availability.reason ?? '没有可安全封闭的进口车道'}`,
    )
  }
  return availability.laneIds
}

export function assertSafeLaneClosureEvents(events: LaneClosureEventLike[]): void {
  const unavailable = events.flatMap((event) => {
    const eventType = event.event_type ?? event.eventType
    if (eventType !== 'lane_closure') return []
    const intersectionIds = event.intersection_ids ?? event.intersectionIds ?? []
    return intersectionIds.flatMap((intersectionId) => {
      const availability = laneClosureAvailability(intersectionId)
      return availability.available
        ? []
        : [{ intersectionId, reason: availability.reason }]
    })
  })
  if (unavailable.length === 0) return
  throw new Error(unavailable.map(({ intersectionId, reason }) => (
    `${formatIntersectionLabel(intersectionId)}不支持施工占道：${reason ?? '没有可安全封闭的进口车道'}`
  )).join('；'))
}
