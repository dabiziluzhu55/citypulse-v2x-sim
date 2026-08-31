import type {
  Point2,
  RealisticIntersectionManifest,
  RealisticRoadSurfaceExclusion,
} from './intersectionManifest'

interface PolylineMetrics {
  cumulative: number[]
  total: number
}

export interface RoadSurfaceVisibilityInterval {
  edgeId: string
  startOffsetMeters: number
  endOffsetMeters: number
}

function metrics(points: Point2[]): PolylineMetrics {
  const cumulative = [0]
  for (let index = 1; index < points.length; index += 1) {
    cumulative.push(cumulative.at(-1)! + Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1],
    ))
  }
  return { cumulative, total: cumulative.at(-1) ?? 0 }
}

function sampleAt(points: Point2[], values: PolylineMetrics, distance: number): Point2 {
  for (let index = 1; index < points.length; index += 1) {
    if (distance > values.cumulative[index] && index < points.length - 1) continue
    const segmentLength = values.cumulative[index] - values.cumulative[index - 1]
    const ratio = segmentLength > 1e-9
      ? (distance - values.cumulative[index - 1]) / segmentLength
      : 0
    return [
      points[index - 1][0] + (points[index][0] - points[index - 1][0]) * ratio,
      points[index - 1][1] + (points[index][1] - points[index - 1][1]) * ratio,
    ]
  }
  return [...points.at(-1)!]
}

function section(points: Point2[], values: PolylineMetrics, start: number, end: number): Point2[] {
  const result = [sampleAt(points, values, start)]
  for (let index = 1; index < points.length - 1; index += 1) {
    if (values.cumulative[index] > start && values.cumulative[index] < end) {
      result.push([...points[index]] as Point2)
    }
  }
  const finalPoint = sampleAt(points, values, end)
  if (Math.hypot(finalPoint[0] - result.at(-1)![0], finalPoint[1] - result.at(-1)![1]) > 1e-6) {
    result.push(finalPoint)
  }
  return result
}

export function normalizedSurfaceExclusions(
  exclusions: RealisticRoadSurfaceExclusion[] | undefined,
  referenceLengthMeters: number,
): Array<[number, number]> {
  if (!exclusions?.length || referenceLengthMeters <= 1e-9) return []
  const sorted = exclusions
    .map((item) => [
      Math.max(0, Math.min(referenceLengthMeters, item.startOffsetMeters)),
      Math.max(0, Math.min(referenceLengthMeters, item.endOffsetMeters)),
    ] as [number, number])
    .filter(([start, end]) => end - start > 1e-6)
    .sort((left, right) => left[0] - right[0])
  const merged: Array<[number, number]> = []
  for (const range of sorted) {
    const previous = merged.at(-1)
    if (!previous || range[0] > previous[1] + 1e-6) merged.push([...range])
    else previous[1] = Math.max(previous[1], range[1])
  }
  return merged
}

export function visiblePolylineSections(
  points: Point2[],
  exclusions: RealisticRoadSurfaceExclusion[] | undefined,
  horizontalScale = 1,
  referencePoints: Point2[] = points,
): Point2[][] {
  if (points.length < 2) return []
  const values = metrics(points)
  const referenceLength = metrics(referencePoints).total
  const referenceLengthMeters = referenceLength / horizontalScale
  const hidden = normalizedSurfaceExclusions(exclusions, referenceLengthMeters)
  if (hidden.length === 0) return [points]
  const visible: Array<[number, number]> = []
  let cursor = 0
  for (const [startMeters, endMeters] of hidden) {
    const start = startMeters / referenceLengthMeters * values.total
    const end = endMeters / referenceLengthMeters * values.total
    if (start > cursor + 1e-6) visible.push([cursor, start])
    cursor = Math.max(cursor, end)
  }
  if (cursor < values.total - 1e-6) visible.push([cursor, values.total])
  return visible
    .map(([start, end]) => section(points, values, start, end))
    .filter((item) => item.length >= 2)
}

export function surfaceOffsetIsExcluded(
  offsetMeters: number,
  exclusions: RealisticRoadSurfaceExclusion[] | undefined,
  referenceLengthMeters: number,
): boolean {
  return normalizedSurfaceExclusions(exclusions, referenceLengthMeters)
    .some(([start, end]) => offsetMeters >= start && offsetMeters <= end)
}

export function fullyExcludedSurfaceEdgeIds(
  manifest: Pick<RealisticIntersectionManifest, 'edges' | 'horizontalScale'>,
): Set<string> {
  const scale = manifest.horizontalScale ?? 1
  return new Set(manifest.edges.flatMap((edge) => {
    const points = edge.centerline ?? edge.lanes[0]?.renderPoints ?? edge.lanes[0]?.points ?? []
    const lengthMeters = metrics(points).total / scale
    const hidden = normalizedSurfaceExclusions(edge.surfaceExclusions, lengthMeters)
    return hidden.length === 1
      && hidden[0][0] <= 1e-6
      && hidden[0][1] >= lengthMeters - 1e-3
      ? [edge.id]
      : []
  }))
}

export function surfaceVisibilityIntervals(
  manifest: Pick<RealisticIntersectionManifest, 'edges' | 'horizontalScale'>,
): RoadSurfaceVisibilityInterval[] {
  const scale = manifest.horizontalScale ?? 1
  return manifest.edges.flatMap((edge) => {
    const points = edge.centerline ?? edge.lanes[0]?.renderPoints ?? edge.lanes[0]?.points ?? []
    const lengthMeters = metrics(points).total / scale
    return normalizedSurfaceExclusions(edge.surfaceExclusions, lengthMeters).map((range) => ({
      edgeId: edge.id,
      startOffsetMeters: range[0],
      endOffsetMeters: range[1],
    }))
  })
}
