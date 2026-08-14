import type { DetectedEventCard } from '../types/intelligence.ts'
import type { SceneEventDetail, SceneEventMarker, SceneEventMarkerColor } from './sceneEventMarkers.ts'

export function detectedMarkerColor(card: DetectedEventCard): SceneEventMarkerColor {
  return String(card.event_type ?? '').toLowerCase() === 'accident' ? 'red' : 'yellow'
}

function timeRangesOverlap(left: SceneEventDetail, right: SceneEventDetail): boolean {
  const range = (detail: SceneEventDetail): [number, number] => detail.kind === 'detected'
    ? [detail.card.start_seconds, detail.card.end_seconds ?? Number.POSITIVE_INFINITY]
    : [detail.event.startSeconds, detail.event.endSeconds]
  const [leftStart, leftEnd] = range(left)
  const [rightStart, rightEnd] = range(right)
  return Math.max(leftStart, rightStart) <= Math.min(leftEnd, rightEnd)
}

function laneIds(detail: SceneEventDetail): string[] {
  if (detail.kind === 'detected') return detail.card.lane_ids
  const values = [
    detail.event.details.lane_id,
    detail.event.details.venue_lane_id,
    detail.event.details.lane_ids,
  ]
  return values.flatMap((value) => Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : typeof value === 'string' ? [value] : [])
}

function laneKey(value: string): string {
  return value.replace(/_\d+$/, '')
}

function shouldMergeAccidents(left: SceneEventMarker, right: SceneEventMarker): boolean {
  if (left.color !== 'red' || right.color !== 'red') return false
  if (left.intersectionId !== right.intersectionId) return false
  const isAccident = (detail: SceneEventDetail) => detail.kind === 'detected'
    ? String(detail.card.event_type).toLowerCase() === 'accident'
    : detail.event.eventType === 'accident'
  if (!left.details.some(isAccident) || !right.details.some(isAccident)) return false
  if (!left.details.some((a) => right.details.some((b) => timeRangesOverlap(a, b)))) return false
  const leftLanes = new Set(left.details.flatMap(laneIds).map(laneKey))
  const sharedLane = right.details.flatMap(laneIds).some((laneId) => leftLanes.has(laneKey(laneId)))
  return sharedLane
}

export function mergeSceneEventMarkers(markers: SceneEventMarker[]): SceneEventMarker[] {
  const merged: SceneEventMarker[] = []
  for (const marker of markers) {
    const existing = merged.find((candidate) => shouldMergeAccidents(candidate, marker))
    if (!existing) {
      merged.push({ ...marker, details: [...marker.details] })
      continue
    }
    existing.details.push(...marker.details)
    if (marker.position.source !== 'intersection_fallback') existing.position = marker.position
    existing.id = existing.details.map((detail) => detail.id).sort().join('+')
  }
  return merged
}
