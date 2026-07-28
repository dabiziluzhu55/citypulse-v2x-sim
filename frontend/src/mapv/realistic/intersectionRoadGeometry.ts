import type {
  Point2,
  RealisticLane,
  RealisticRoadEdge,
} from './intersectionManifest'

const MIN_COMMON_SAMPLES = 20
const MAX_COMMON_SAMPLES = 96

function distance(a: Point2, b: Point2): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1])
}

function polylineLengths(points: Point2[]): { cumulative: number[]; total: number } {
  const cumulative = [0]
  for (let index = 1; index < points.length; index += 1) {
    cumulative.push(cumulative.at(-1)! + distance(points[index - 1], points[index]))
  }
  return { cumulative, total: cumulative.at(-1) ?? 0 }
}

export function samplePolyline(points: Point2[], progress: number): Point2 {
  if (points.length === 0) return [0, 0]
  if (points.length === 1) return [...points[0]]
  const { cumulative, total } = polylineLengths(points)
  const target = Math.max(0, Math.min(1, progress)) * total
  for (let index = 1; index < points.length; index += 1) {
    if (target > cumulative[index] && index < points.length - 1) continue
    const segment = cumulative[index] - cumulative[index - 1]
    const ratio = segment > 1e-6 ? (target - cumulative[index - 1]) / segment : 0
    return [
      points[index - 1][0] + (points[index][0] - points[index - 1][0]) * ratio,
      points[index - 1][1] + (points[index][1] - points[index - 1][1]) * ratio,
    ]
  }
  return [...points.at(-1)!]
}

export function visualLanePoints(lane: RealisticLane): Point2[] {
  return lane.renderPoints?.length ? lane.renderPoints : lane.points
}

function sampleCount(lanes: RealisticLane[]): number {
  const largest = Math.max(...lanes.map((lane) => lane.points.length), MIN_COMMON_SAMPLES)
  return Math.min(MAX_COMMON_SAMPLES, largest)
}

function normalAt(points: Point2[], index: number): Point2 {
  const previous = points[Math.max(0, index - 1)]
  const next = points[Math.min(points.length - 1, index + 1)]
  const dx = next[0] - previous[0]
  const dy = next[1] - previous[1]
  const magnitude = Math.hypot(dx, dy) || 1
  return [-dy / magnitude, dx / magnitude]
}

export interface RebuiltRoadEdgeGeometry {
  centerline: Point2[]
  roadWidth: number
  renderPoints: Point2[][]
}

export function rebuildRoadEdgeGeometry(lanes: RealisticLane[]): RebuiltRoadEdgeGeometry {
  if (lanes.length === 0) return { centerline: [], roadWidth: 0, renderPoints: [] }
  const count = sampleCount(lanes)
  const samples = lanes.map((lane) => Array.from({ length: count }, (_, index) => (
    samplePolyline(lane.points, index / (count - 1))
  )))
  const centerline = Array.from({ length: count }, (_, pointIndex) => {
    const sum = samples.reduce((value, lane) => (
      [value[0] + lane[pointIndex][0], value[1] + lane[pointIndex][1]] as Point2
    ), [0, 0] as Point2)
    return [sum[0] / lanes.length, sum[1] / lanes.length] as Point2
  })
  const widths = lanes.map((lane) => lane.width)
  const roadWidth = widths.reduce((sum, width) => sum + width, 0)
  const offsets: number[] = []
  let cursor = -roadWidth / 2
  for (const width of widths) {
    offsets.push(cursor + width / 2)
    cursor += width
  }
  const middle = Math.floor(count / 2)
  const referenceNormal = normalAt(centerline, middle)
  const laneOrderVector: Point2 = [
    samples.at(-1)![middle][0] - samples[0][middle][0],
    samples.at(-1)![middle][1] - samples[0][middle][1],
  ]
  const order = laneOrderVector[0] * referenceNormal[0] + laneOrderVector[1] * referenceNormal[1]
  const orientation = order >= 0 ? 1 : -1
  const renderPoints = lanes.map((_, laneIndex) => centerline.map((point, pointIndex) => {
    const normal = normalAt(centerline, pointIndex)
    const offset = offsets[laneIndex] * orientation
    return [point[0] + normal[0] * offset, point[1] + normal[1] * offset] as Point2
  }))
  return { centerline, roadWidth, renderPoints }
}

export function edgeCenterline(edge: RealisticRoadEdge): Point2[] {
  if (edge.centerline?.length) return edge.centerline
  return rebuildRoadEdgeGeometry(edge.lanes).centerline
}

export function edgeRoadWidth(edge: RealisticRoadEdge): number {
  return edge.roadWidth ?? edge.lanes.reduce((sum, lane) => sum + lane.width, 0)
}

export function nearestPolylineProgress(point: Point2, points: Point2[]): {
  progress: number
  distance: number
} | null {
  if (points.length < 2) return null
  const { cumulative, total } = polylineLengths(points)
  let bestDistance = Number.POSITIVE_INFINITY
  let bestProgress = 0
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1]
    const end = points[index]
    const dx = end[0] - start[0]
    const dy = end[1] - start[1]
    const lengthSquared = dx * dx + dy * dy
    const ratio = lengthSquared > 1e-9
      ? Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared))
      : 0
    const projected: Point2 = [start[0] + dx * ratio, start[1] + dy * ratio]
    const candidateDistance = distance(point, projected)
    if (candidateDistance >= bestDistance) continue
    bestDistance = candidateDistance
    const segmentLength = Math.sqrt(lengthSquared)
    bestProgress = total > 1e-6
      ? (cumulative[index - 1] + segmentLength * ratio) / total
      : 0
  }
  return { progress: bestProgress, distance: bestDistance }
}

export function tangentAtProgress(points: Point2[], progress: number): number | null {
  if (points.length < 2) return null
  const before = samplePolyline(points, Math.max(0, progress - 0.002))
  const after = samplePolyline(points, Math.min(1, progress + 0.002))
  const dx = after[0] - before[0]
  const dy = after[1] - before[1]
  return Math.hypot(dx, dy) > 1e-6 ? Math.atan2(dy, dx) : null
}

export function convexHull(points: Point2[]): Point2[] {
  const unique = [...new Map(points.map((point) => [`${point[0].toFixed(4)}:${point[1].toFixed(4)}`, point])).values()]
    .sort((left, right) => left[0] - right[0] || left[1] - right[1])
  if (unique.length <= 2) return unique
  const cross = (origin: Point2, a: Point2, b: Point2) => (
    (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])
  )
  const half = (values: Point2[]) => {
    const result: Point2[] = []
    for (const point of values) {
      while (result.length >= 2 && cross(result.at(-2)!, result.at(-1)!, point) <= 0) result.pop()
      result.push(point)
    }
    return result
  }
  return [...half(unique).slice(0, -1), ...half([...unique].reverse()).slice(0, -1)]
}

export function junctionApronPoints(junctionShape: Point2[], edges: RealisticRoadEdge[]): Point2[] {
  const boundary = [...junctionShape]
  for (const edge of edges) {
    const centerline = edgeCenterline(edge)
    if (centerline.length < 2) continue
    const endpointIndex = edge.incoming ? centerline.length - 1 : 0
    const endpoint = centerline[endpointIndex]
    const normal = normalAt(centerline, endpointIndex)
    const halfWidth = edgeRoadWidth(edge) / 2
    boundary.push(
      [endpoint[0] + normal[0] * halfWidth, endpoint[1] + normal[1] * halfWidth],
      [endpoint[0] - normal[0] * halfWidth, endpoint[1] - normal[1] * halfWidth],
    )
  }
  return convexHull(boundary)
}

export function expandPolygon(points: Point2[], padding: number): Point2[] {
  const center = points.reduce((sum, point) => (
    [sum[0] + point[0], sum[1] + point[1]] as Point2
  ), [0, 0] as Point2)
  center[0] /= points.length || 1
  center[1] /= points.length || 1
  return points.map((point) => {
    const dx = point[0] - center[0]
    const dy = point[1] - center[1]
    const magnitude = Math.hypot(dx, dy) || 1
    return [point[0] + dx / magnitude * padding, point[1] + dy / magnitude * padding]
  })
}
