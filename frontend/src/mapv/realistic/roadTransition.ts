import type { Point2 } from './intersectionManifest'

export interface RoadTransitionSection {
  points: Point2[]
  opacity: number
}

export interface RoadBoundaryFadeFlags {
  fadeStart: boolean
  fadeEnd: boolean
}

const BOUNDARY_RADIUS_RATIO = 0.995

export function roadBoundaryFadeFlags(
  points: Point2[],
  radiusSceneUnits: number,
): RoadBoundaryFadeFlags {
  if (points.length < 2 || !Number.isFinite(radiusSceneUnits) || radiusSceneUnits <= 0) {
    return { fadeStart: false, fadeEnd: false }
  }
  const reachesBoundary = (point: Point2) => (
    Math.hypot(point[0], point[1]) >= radiusSceneUnits * BOUNDARY_RADIUS_RATIO
  )
  return {
    fadeStart: reachesBoundary(points[0]),
    fadeEnd: reachesBoundary(points.at(-1)!),
  }
}

function lengths(points: Point2[]): { cumulative: number[]; total: number } {
  const cumulative = [0]
  for (let index = 1; index < points.length; index += 1) {
    cumulative.push(cumulative.at(-1)! + Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1],
    ))
  }
  return { cumulative, total: cumulative.at(-1) ?? 0 }
}

function sampleAt(points: Point2[], cumulative: number[], distance: number): Point2 {
  for (let index = 1; index < points.length; index += 1) {
    if (distance > cumulative[index] && index < points.length - 1) continue
    const segment = cumulative[index] - cumulative[index - 1]
    const ratio = segment > 1e-6 ? (distance - cumulative[index - 1]) / segment : 0
    return [
      points[index - 1][0] + (points[index][0] - points[index - 1][0]) * ratio,
      points[index - 1][1] + (points[index][1] - points[index - 1][1]) * ratio,
    ]
  }
  return [...points.at(-1)!]
}

function section(points: Point2[], cumulative: number[], start: number, end: number): Point2[] {
  const result: Point2[] = [sampleAt(points, cumulative, start)]
  for (let index = 1; index < points.length - 1; index += 1) {
    if (cumulative[index] > start && cumulative[index] < end) result.push([...points[index]])
  }
  const finalPoint = sampleAt(points, cumulative, end)
  const previous = result.at(-1)!
  if (Math.hypot(finalPoint[0] - previous[0], finalPoint[1] - previous[1]) > 1e-6) {
    result.push(finalPoint)
  }
  return result
}

export function buildRoadTransitionSections(
  points: Point2[],
  fadeStart: boolean,
  fadeEnd: boolean,
  requestedLength: number,
): RoadTransitionSection[] {
  if (points.length < 2) return []
  const { cumulative, total } = lengths(points)
  if (total <= 1e-6 || (!fadeStart && !fadeEnd)) return [{ points, opacity: 1 }]
  const transitionLength = Math.max(0, Math.min(requestedLength, total * 0.45))
  if (transitionLength <= 1e-6) return [{ points, opacity: 1 }]
  const stops = new Set([0, total])
  for (const ratio of [1 / 3, 2 / 3, 1]) {
    if (fadeStart) stops.add(transitionLength * ratio)
    if (fadeEnd) stops.add(total - transitionLength * ratio)
  }
  const sorted = [...stops].sort((left, right) => left - right)
  const sections: RoadTransitionSection[] = []
  for (let index = 1; index < sorted.length; index += 1) {
    const start = sorted[index - 1]
    const end = sorted[index]
    if (end - start <= 1e-6) continue
    const midpoint = (start + end) / 2
    let opacity = 1
    if (fadeStart && midpoint < transitionLength) opacity = Math.min(opacity, midpoint / transitionLength)
    if (fadeEnd && midpoint > total - transitionLength) opacity = Math.min(opacity, (total - midpoint) / transitionLength)
    sections.push({
      points: section(points, cumulative, start, end),
      opacity: Math.max(0.18, Math.min(1, opacity)),
    })
  }
  return sections
}
