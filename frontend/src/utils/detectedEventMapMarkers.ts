import type { DetectedEventCard } from '../types/intelligence'

const EARTH_RADIUS_METERS = 6_371_000

export interface DetectedEventMapMarker {
  id: string
  longitude: number
  latitude: number
  cards: DetectedEventCard[]
}

function distanceMeters(
  left: readonly [number, number],
  right: readonly [number, number],
): number {
  const latitude = (left[1] + right[1]) * Math.PI / 360
  const dx = (right[0] - left[0]) * Math.PI / 180 * Math.cos(latitude)
  const dy = (right[1] - left[1]) * Math.PI / 180
  return Math.hypot(dx, dy) * EARTH_RADIUS_METERS
}

export function groupDetectedEventMapMarkers(
  cards: readonly DetectedEventCard[],
  mergeDistanceMeters = 8,
): DetectedEventMapMarker[] {
  const markers: DetectedEventMapMarker[] = []
  for (const card of cards) {
    const longitude = Number(card.longitude)
    const latitude = Number(card.latitude)
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) continue
    const existing = markers.find((marker) => distanceMeters(
      [marker.longitude, marker.latitude],
      [longitude, latitude],
    ) <= mergeDistanceMeters)
    if (existing) {
      existing.cards.push(card)
      existing.id = [...existing.cards]
        .map((item) => item.event_id)
        .sort()
        .join('|')
      continue
    }
    markers.push({
      id: card.event_id,
      longitude,
      latitude,
      cards: [card],
    })
  }
  return markers
}
