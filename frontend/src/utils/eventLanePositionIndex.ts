import type { IntersectionTopologyNode } from '../mapv/intersectionTopology.ts'
import type { DisturbanceRuntimeView } from './runtimeDisturbances.ts'
import { runtimeDisturbanceLaneIds } from './runtimeDisturbances.ts'

export type EventLaneKind = 'driving' | 'bicycle' | 'pedestrian'

export interface EventLanePositionIndexEntry {
  laneId: string
  edgeId: string
  kind: EventLaneKind
  intersectionId: string
  coordinates: Array<[number, number]>
}

export interface EventLanePositionIndex {
  schemaVersion: 1
  networkSource: { path: string; sha256: string }
  laneCount: number
  entries: EventLanePositionIndexEntry[]
}

export type SessionEventPositionSource =
  | 'accident_lane'
  | 'venue_lane'
  | 'affected_lane'
  | 'intersection_fallback'

export interface SessionEventMarkerPosition {
  longitude: number
  latitude: number
  intersectionId: string
  laneId?: string
  positionRatio?: number
  source: SessionEventPositionSource
  fallbackReason?: string
}

export interface SessionEventMarker {
  id: string
  color: 'red' | 'orange' | 'blue'
  position: SessionEventMarkerPosition
  events: DisturbanceRuntimeView[]
}

let indexPromise: Promise<EventLanePositionIndex> | null = null

function finiteCoordinate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function parseEventLanePositionIndex(value: unknown): EventLanePositionIndex {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Event lane position index must be an object')
  }
  const source = value as Partial<EventLanePositionIndex>
  if (
    source.schemaVersion !== 1
    || !source.networkSource
    || !/^[a-f0-9]{64}$/.test(source.networkSource.sha256)
    || !Array.isArray(source.entries)
    || source.laneCount !== source.entries.length
  ) {
    throw new Error('Event lane position index is incompatible')
  }
  const laneIds = new Set<string>()
  for (const entry of source.entries) {
    if (
      !entry
      || typeof entry.laneId !== 'string'
      || laneIds.has(entry.laneId)
      || typeof entry.edgeId !== 'string'
      || typeof entry.intersectionId !== 'string'
      || !['driving', 'bicycle', 'pedestrian'].includes(entry.kind)
      || !Array.isArray(entry.coordinates)
      || entry.coordinates.length < 2
      || !entry.coordinates.every((point) => (
        Array.isArray(point)
        && point.length >= 2
        && finiteCoordinate(point[0])
        && finiteCoordinate(point[1])
      ))
    ) {
      throw new Error('Event lane position index contains an invalid lane')
    }
    laneIds.add(entry.laneId)
  }
  return source as EventLanePositionIndex
}

export async function loadEventLanePositionIndex(
  url = '/intersections/v3/event-lane-position-index.json',
): Promise<EventLanePositionIndex> {
  indexPromise ??= fetch(url).then(async (response) => {
    if (!response.ok) throw new Error(`Event lane position index returned HTTP ${response.status}`)
    return parseEventLanePositionIndex(await response.json())
  })
  return indexPromise
}

export function sampleEventLanePosition(
  entry: EventLanePositionIndexEntry,
  ratio: number,
): [number, number] {
  const progress = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0.5))
  const lengths = [0]
  for (let index = 1; index < entry.coordinates.length; index += 1) {
    const left = entry.coordinates[index - 1]
    const right = entry.coordinates[index]
    const latitude = (left[1] + right[1]) * Math.PI / 360
    lengths.push(lengths[index - 1] + Math.hypot(
      (right[0] - left[0]) * 111_320 * Math.cos(latitude),
      (right[1] - left[1]) * 110_574,
    ))
  }
  const target = lengths[lengths.length - 1] * progress
  const upper = Math.max(1, lengths.findIndex((value) => value >= target))
  const startLength = lengths[upper - 1]
  const segmentLength = Math.max(1e-9, lengths[upper] - startLength)
  const local = (target - startLength) / segmentLength
  const start = entry.coordinates[upper - 1]
  const end = entry.coordinates[upper]
  return [
    start[0] + (end[0] - start[0]) * local,
    start[1] + (end[1] - start[1]) * local,
  ]
}

function eventPositionRatio(event: DisturbanceRuntimeView): number {
  if (event.eventType.startsWith('major_event_')) return 0.5
  const raw = Number(event.details.position_ratio ?? event.parameters.position_ratio)
  return Number.isFinite(raw) ? Math.max(0, Math.min(1, raw)) : 0.5
}

function preferredLaneIds(event: DisturbanceRuntimeView): string[] {
  const preferred = event.eventType === 'accident'
    ? [event.details.lane_id, event.parameters.lane_id]
    : event.eventType.startsWith('major_event_')
      ? [event.details.venue_lane_id, event.parameters.venue_lane_id]
      : []
  return [...new Set([
    ...preferred.filter((value): value is string => typeof value === 'string' && Boolean(value)),
    ...runtimeDisturbanceLaneIds(event),
  ])]
}

function markerColor(event: DisturbanceRuntimeView): SessionEventMarker['color'] {
  if (event.eventType === 'accident' || event.eventType.startsWith('major_event_')) return 'red'
  if (event.eventType === 'speed_limit') return 'orange'
  return 'red'
}

export function resolveSessionEventMarkers(
  events: readonly DisturbanceRuntimeView[],
  index: EventLanePositionIndex | null,
  intersections: readonly IntersectionTopologyNode[],
  fallbackIntersectionId = '',
): SessionEventMarker[] {
  const lanes = new Map(index?.entries.map((entry) => [entry.laneId, entry] as const) ?? [])
  const nodes = new Map(intersections.map((entry) => [entry.intersectionId, entry] as const))
  const resolved = events.flatMap((event): SessionEventMarker[] => {
    if (event.state === 'CANCELLED') return []
    const ratio = eventPositionRatio(event)
    const lane = preferredLaneIds(event).map((laneId) => lanes.get(laneId)).find(Boolean)
    if (lane) {
      const [longitude, latitude] = sampleEventLanePosition(lane, ratio)
      return [{
        id: `runtime:${event.eventId}`,
        color: markerColor(event),
        position: {
          longitude,
          latitude,
          intersectionId: lane.intersectionId || event.intersectionId,
          laneId: lane.laneId,
          positionRatio: ratio,
          source: event.eventType === 'accident'
            ? 'accident_lane'
            : event.eventType.startsWith('major_event_') ? 'venue_lane' : 'affected_lane',
        },
        events: [event],
      }]
    }
    const intersectionId = event.intersectionId || fallbackIntersectionId
    const node = nodes.get(intersectionId)
    if (!node) return []
    return [{
      id: `runtime:${event.eventId}`,
      color: markerColor(event),
      position: {
        longitude: node.longitude,
        latitude: node.latitude,
        intersectionId,
        source: 'intersection_fallback',
        fallbackReason: preferredLaneIds(event).length ? 'lane_not_indexed' : 'lane_id_missing',
      },
      events: [event],
    }]
  })

  const merged = new Map<string, SessionEventMarker>()
  for (const marker of resolved) {
    const key = [
      marker.position.longitude.toFixed(6),
      marker.position.latitude.toFixed(6),
      marker.color,
    ].join(':')
    const existing = merged.get(key)
    if (!existing) {
      merged.set(key, marker)
      continue
    }
    existing.events.push(...marker.events)
    existing.id = existing.events.map((event) => event.eventId).sort().join('+')
  }
  return [...merged.values()]
}
