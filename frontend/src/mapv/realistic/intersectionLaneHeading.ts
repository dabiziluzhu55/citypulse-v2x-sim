import type { RealisticIntersectionManifest, RealisticLane } from './intersectionManifest.ts'
import type { RoadCoordinateProjector } from '../roadGeometry.ts'
import {
  projectBd09ToWebMercator,
  unprojectWebMercatorToBd09,
} from '../sceneCoordinates.ts'
import {
  nearestPolylineProgress,
  samplePolyline,
  tangentAtProgress,
  visualLanePoints,
} from './intersectionRoadGeometry.ts'

export type LaneHeadingResolver = (laneId: string, lanePosition?: number) => number | null
export interface ResolvedLanePose {
  longitude: number
  latitude: number
  heading: number
  modelCenterResolved: boolean
  trackKey: string
}
export type LanePoseResolver = (
  laneId: string,
  coordinate: readonly [number, number],
  modelCenterOffsetMeters?: number,
  previousLaneId?: string,
  previousTrackKey?: string,
) => ResolvedLanePose | null

interface LaneTrack {
  key: string
  laneId: string
  sourcePoints: [number, number][]
  renderPoints: [number, number][]
  beforeRenderPoints?: [number, number][]
  routeKey?: string
  routeLaneIds: string[]
}

function polylineLength(points: [number, number][]): number {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
}

export function mapSourceProgressToRenderDistance(
  progress: number,
  sourceLength: number,
  renderLength: number,
): number {
  const x = Math.max(0, Math.min(1, progress))
  if (sourceLength <= 1e-6 || renderLength <= 1e-6) return x * renderLength
  const endpointSlope = sourceLength / renderLength
  const x2 = x * x
  const x3 = x2 * x
  const normalizedDistance = (
    (-2 * x3 + 3 * x2)
    + (2 * x3 - 3 * x2 + x) * endpointSlope
  )
  return Math.max(0, Math.min(renderLength, normalizedDistance * renderLength))
}

function joinPolylines(...polylines: Array<[number, number][]>): [number, number][] {
  const joined: [number, number][] = []
  for (const points of polylines) {
    for (const point of points) {
      const previous = joined.at(-1)
      if (previous && Math.hypot(point[0] - previous[0], point[1] - previous[1]) <= 0.001) continue
      joined.push(point)
    }
  }
  return joined
}

function laneHeadingAtPosition(lane: RealisticLane, lanePosition = 0): number | null {
  const points = visualLanePoints(lane)
  if (points.length < 2) return null
  const scale = lane.widthMeters && lane.width > 0 ? lane.width / lane.widthMeters : 1
  let remaining = Math.max(0, lanePosition * scale)
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    const dx = current[0] - previous[0]
    const dy = current[1] - previous[1]
    const length = Math.hypot(dx, dy)
    if (length <= 1e-6) continue
    if (remaining <= length || index === points.length - 1) return Math.atan2(dy, dx)
    remaining -= length
  }
  return null
}

export function createIntersectionLanePoseResolver(
  manifest: Pick<RealisticIntersectionManifest, 'edges' | 'connections' | 'origin' | 'horizontalScale'>,
  projector: RoadCoordinateProjector,
): LanePoseResolver {
  const tracks = new Map<string, LaneTrack[]>()
  const tracksByKey = new Map<string, LaneTrack>()
  const addTrack = (track: LaneTrack) => {
    const candidates = tracks.get(track.laneId) ?? []
    candidates.push(track)
    tracks.set(track.laneId, candidates)
    tracksByKey.set(track.key, track)
  }
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      if (!lane.renderPoints?.length) continue
      addTrack({
        key: `lane:${lane.id}`,
        laneId: lane.id,
        sourcePoints: lane.points,
        renderPoints: lane.renderPoints,
        routeLaneIds: [lane.id],
      })
    }
  }
  for (const connection of manifest.connections) {
    const fromEdge = manifest.edges.find((edge) => edge.id === connection.fromEdge)
    const fromLane = fromEdge?.lanes.find((lane) => lane.index === connection.fromLane)
    const toEdge = manifest.edges.find((edge) => edge.id === connection.toEdge)
    const toLane = toEdge?.lanes.find((lane) => lane.index === connection.toLane)
    const segments = connection.viaSegments?.length
      ? connection.viaSegments
      : connection.viaLaneId && connection.viaPoints?.length && connection.renderPoints?.length
        ? [{
            laneId: connection.viaLaneId,
            points: connection.viaPoints,
            renderPoints: connection.renderPoints,
          }]
        : []
    if (!fromLane?.renderPoints?.length || !toLane?.renderPoints?.length || segments.length === 0) continue
    const routeKey = `${connection.tlsId}:${connection.linkIndex}`
    const routeLaneIds = [fromLane.id, ...segments.map((segment) => segment.laneId), toLane.id]
    let beforeRenderPoints = fromLane.renderPoints
    for (const segment of segments) {
      addTrack({
        key: `${routeKey}:${segment.laneId}`,
        laneId: segment.laneId,
        sourcePoints: segment.points,
        renderPoints: segment.renderPoints,
        beforeRenderPoints,
        routeKey,
        routeLaneIds,
      })
      beforeRenderPoints = joinPolylines(beforeRenderPoints, segment.renderPoints)
    }
    addTrack({
      key: `${routeKey}:${toLane.id}`,
      laneId: toLane.id,
      sourcePoints: toLane.points,
      renderPoints: toLane.renderPoints,
      beforeRenderPoints,
      routeKey,
      routeLaneIds,
    })
  }
  const projectedOrigin = projector([
    manifest.origin.longitude,
    manifest.origin.latitude,
    0,
  ])
  const originPlane = projectBd09ToWebMercator([projectedOrigin[0], projectedOrigin[1]])
  const maximumSnapDistance = 12 * (manifest.horizontalScale ?? 1)
  return (
    laneId,
    coordinate,
    modelCenterOffsetMeters = 0,
    previousLaneId,
    previousTrackKey,
  ) => {
    const candidates = tracks.get(laneId)
    if (!candidates?.length) return null
    const projected = projectBd09ToWebMercator(coordinate)
    const local: [number, number] = [
      projected[0] - originPlane[0],
      projected[1] - originPlane[1],
    ]
    const previousTrack = previousTrackKey ? tracksByKey.get(previousTrackKey) : undefined
    const routeCandidates = previousTrack?.routeKey
      ? candidates.filter((candidate) => candidate.routeKey === previousTrack.routeKey)
      : previousLaneId
        ? candidates.filter((candidate) => candidate.routeLaneIds.includes(previousLaneId))
        : []
    const resolvedCandidates = (routeCandidates.length ? routeCandidates : candidates)
      .map((track) => ({ track, nearest: nearestPolylineProgress(local, track.sourcePoints) }))
      .filter((candidate) => candidate.nearest !== null)
      .sort((left, right) => left.nearest!.distance - right.nearest!.distance)
    const resolved = resolvedCandidates[0]
    if (!resolved || resolved.nearest!.distance > maximumSnapDistance) return null
    const { track } = resolved
    const nearest = resolved.nearest!
    const sourceLength = polylineLength(track.sourcePoints)
    const renderLength = polylineLength(track.renderPoints)
    const backtrackSceneUnits = Math.max(0, modelCenterOffsetMeters) * (manifest.horizontalScale ?? 1)
    const targetDistance = mapSourceProgressToRenderDistance(
      nearest.progress,
      sourceLength,
      renderLength,
    ) - backtrackSceneUnits
    let activePoints = track.renderPoints
    let renderProgress = renderLength > 1e-6 ? Math.max(0, targetDistance / renderLength) : nearest.progress
    if (targetDistance < 0 && track.beforeRenderPoints?.length) {
      const beforeLength = polylineLength(track.beforeRenderPoints)
      activePoints = track.beforeRenderPoints
      renderProgress = beforeLength > 1e-6
        ? Math.max(0, 1 + targetDistance / beforeLength)
        : 1
    }
    const renderPoint = samplePolyline(activePoints, renderProgress)
    const heading = tangentAtProgress(activePoints, renderProgress)
    if (heading == null) return null
    const [longitude, latitude] = unprojectWebMercatorToBd09([
      originPlane[0] + renderPoint[0],
      originPlane[1] + renderPoint[1],
    ])
    return {
      longitude,
      latitude,
      heading,
      modelCenterResolved: modelCenterOffsetMeters > 0,
      trackKey: track.key,
    }
  }
}

export function createIntersectionLaneHeadingResolver(
  manifest: Pick<RealisticIntersectionManifest, 'edges'>,
): LaneHeadingResolver {
  const lanes = new Map(
    manifest.edges.flatMap((edge) => edge.lanes.map((lane) => [lane.id, lane] as const)),
  )
  return (laneId, lanePosition) => {
    const lane = lanes.get(laneId)
    return lane ? laneHeadingAtPosition(lane, lanePosition) : null
  }
}
