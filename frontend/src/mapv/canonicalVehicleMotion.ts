import type { TrafficVehicleView } from '../types/traffic'
import type { EventLanePositionIndexEntry } from '../utils/eventLanePositionIndex'

interface LaneTrack {
  coordinates: Array<[number, number]>
  cumulativeMeters: number[]
  lengthMeters: number
}

let laneTracks = new Map<string, LaneTrack>()

function distanceMeters(left: readonly number[], right: readonly number[]): number {
  const latitude = (left[1] + right[1]) * Math.PI / 360
  return Math.hypot(
    (right[0] - left[0]) * 111_320 * Math.cos(latitude),
    (right[1] - left[1]) * 110_574,
  )
}

function buildTrack(entry: EventLanePositionIndexEntry): LaneTrack {
  const cumulativeMeters = [0]
  for (let index = 1; index < entry.coordinates.length; index += 1) {
    cumulativeMeters.push(
      cumulativeMeters[index - 1]
      + distanceMeters(entry.coordinates[index - 1], entry.coordinates[index]),
    )
  }
  return {
    coordinates: entry.coordinates.map((point) => [point[0], point[1]]),
    cumulativeMeters,
    lengthMeters: cumulativeMeters.at(-1) ?? 0,
  }
}

export function registerCanonicalVehicleLaneGeometry(
  entries: readonly EventLanePositionIndexEntry[],
): void {
  laneTracks = new Map(entries
    .filter((entry) => entry.kind === 'driving' && entry.coordinates.length >= 2)
    .map((entry) => [entry.laneId, buildTrack(entry)] as const))
}

function projectStation(
  track: LaneTrack,
  point: readonly [number, number],
): number | null {
  let bestDistance = Number.POSITIVE_INFINITY
  let bestStation = 0
  const latitude = point[1] * Math.PI / 180
  const longitudeScale = 111_320 * Math.cos(latitude)
  for (let index = 1; index < track.coordinates.length; index += 1) {
    const start = track.coordinates[index - 1]
    const end = track.coordinates[index]
    const sx = (start[0] - point[0]) * longitudeScale
    const sy = (start[1] - point[1]) * 110_574
    const ex = (end[0] - point[0]) * longitudeScale
    const ey = (end[1] - point[1]) * 110_574
    const dx = ex - sx
    const dy = ey - sy
    const lengthSquared = dx * dx + dy * dy
    const ratio = lengthSquared > 1e-9
      ? Math.max(0, Math.min(1, -(sx * dx + sy * dy) / lengthSquared))
      : 0
    const px = sx + dx * ratio
    const py = sy + dy * ratio
    const candidate = Math.hypot(px, py)
    if (candidate >= bestDistance) continue
    bestDistance = candidate
    bestStation = track.cumulativeMeters[index - 1] + Math.sqrt(lengthSquared) * ratio
  }
  return bestDistance <= 12 ? bestStation : null
}

function sampleStation(track: LaneTrack, stationMeters: number): [number, number] {
  const station = Math.max(0, Math.min(track.lengthMeters, stationMeters))
  for (let index = 1; index < track.coordinates.length; index += 1) {
    if (station > track.cumulativeMeters[index] && index < track.coordinates.length - 1) continue
    const span = track.cumulativeMeters[index] - track.cumulativeMeters[index - 1]
    const ratio = span > 1e-9 ? (station - track.cumulativeMeters[index - 1]) / span : 0
    const start = track.coordinates[index - 1]
    const end = track.coordinates[index]
    return [
      start[0] + (end[0] - start[0]) * ratio,
      start[1] + (end[1] - start[1]) * ratio,
    ]
  }
  const last = track.coordinates.at(-1) ?? [0, 0]
  return [last[0], last[1]]
}

export type CanonicalVehicleInterpolationSource = 'lane_frenet' | 'authoritative_linear'

export interface CanonicalVehicleInterpolation {
  longitude: number | null
  latitude: number | null
  source: CanonicalVehicleInterpolationSource
}

export function interpolateCanonicalVehiclePosition(
  left: TrafficVehicleView,
  right: TrafficVehicleView,
  ratio: number,
): CanonicalVehicleInterpolation {
  const amount = Math.max(0, Math.min(1, ratio))
  if (
    left.longitude == null
    || left.latitude == null
    || right.longitude == null
    || right.latitude == null
  ) {
    return {
      longitude: left.longitude ?? right.longitude,
      latitude: left.latitude ?? right.latitude,
      source: 'authoritative_linear',
    }
  }
  const track = left.lane_id === right.lane_id ? laneTracks.get(left.lane_id) : null
  if (track) {
    const leftStation = projectStation(track, [left.longitude, left.latitude])
    const rightStation = projectStation(track, [right.longitude, right.latitude])
    if (
      leftStation != null
      && rightStation != null
      && rightStation + 0.25 >= leftStation
    ) {
      const point = sampleStation(track, leftStation + (rightStation - leftStation) * amount)
      return { longitude: point[0], latitude: point[1], source: 'lane_frenet' }
    }
  }
  return {
    longitude: left.longitude + (right.longitude - left.longitude) * amount,
    latitude: left.latitude + (right.latitude - left.latitude) * amount,
    source: 'authoritative_linear',
  }
}

export function canonicalVehicleLaneGeometryCount(): number {
  return laneTracks.size
}
