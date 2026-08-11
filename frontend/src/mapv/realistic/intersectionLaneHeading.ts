import type { RealisticIntersectionManifest, RealisticLane } from './intersectionManifest.ts'
import type { SimulationLaneRuntime } from '../../types/simulation.ts'
import type { RoadCoordinateProjector } from '../roadGeometry.ts'
import { shortestAngleDelta } from '../vehicleOrientation.ts'
import {
  buildCollisionFreeIntersectionApproaches,
  buildIntersectionApproachGeometry,
} from './intersectionApproachGeometry.ts'
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
export type LanePoseTransitionKind = 'same_lane' | 'topological' | 'lane_change' | 'initial'
export interface ResolvedLanePose {
  longitude: number
  latitude: number
  heading: number
  modelCenterResolved: boolean
  trackKey: string
  motionPathKey: string
  segmentKey: string
  occupancyKey: string
  trackProgress: number
  arcDistanceMeters: number
  pathArcDistanceMeters: number
  minimumArcDistanceMeters: number
  matchConfidence: number
  transitionKind: LanePoseTransitionKind
  modelCenterDistanceMeters: number
  naturalFrontDistanceMeters: number
  stopFrontLimitDistanceMeters?: number
  stopClamped: boolean
}
export interface LanePoseRuntimeOptions {
  speedMetersPerSecond?: number
  expectedHeading?: number | null
  laneRuntime?: SimulationLaneRuntime | null
  previousTrackProgress?: number
  allowStopClamp?: boolean
  minimumModelCenterDistanceMeters?: number
  maximumModelCenterDistanceMeters?: number
  maximumSnapDistanceMeters?: number
  relaxedTrackContinuity?: boolean
}
export interface MotionPathProjection {
  pathArcDistanceMeters: number
  distanceMeters: number
}

export interface MotionPathSample {
  longitude: number
  latitude: number
  heading: number
  pathArcDistanceMeters: number
}

export interface MotionPathSampler {
  project(
    motionPathKey: string,
    coordinate: readonly [number, number],
  ): MotionPathProjection | null
  sample(motionPathKey: string, pathArcDistanceMeters: number): MotionPathSample | null
}

export interface LanePoseResolver {
  (
    laneId: string,
    coordinate: readonly [number, number],
    modelCenterOffsetMeters?: number,
    previousLaneId?: string,
    previousTrackKey?: string,
    options?: LanePoseRuntimeOptions,
  ): ResolvedLanePose | null
  motionPathSampler: MotionPathSampler
}

export const VISUAL_STOP_BOUNDARY_CLEARANCE_METERS = 0.25
export const VISUAL_STOP_LINE_HALF_WIDTH_METERS = 0.21
export const MAX_MOTION_PATH_HEADING_DELTA = 35 * Math.PI / 180

export function visualStopFrontLimitDistance(
  stopBoundaryDistance: number,
  horizontalScale: number,
): number {
  return stopBoundaryDistance - (
    VISUAL_STOP_LINE_HALF_WIDTH_METERS
    + VISUAL_STOP_BOUNDARY_CLEARANCE_METERS
  ) * horizontalScale
}

interface LaneTrack {
  key: string
  motionPathKey: string
  segmentKey: string
  occupancyKey: string
  laneId: string
  edgeId: string
  laneIndex: number
  sourcePoints: [number, number][]
  renderPoints: [number, number][]
  beforeRenderPoints?: [number, number][]
  routeKey?: string
  routeLaneIds: string[]
  routeSegmentIndex?: number
  stopBoundaryDistance?: number
}

interface MotionPathGeometry {
  points: [number, number][]
  lengthSceneUnits: number
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

function connectorPolyline(
  incoming: [number, number][],
  outgoing: [number, number][],
): [number, number][] {
  if (incoming.length < 2 || outgoing.length < 2) return []
  const start = incoming.at(-1)!
  const end = outgoing[0]
  const gap = Math.hypot(end[0] - start[0], end[1] - start[1])
  if (gap <= 0.05) return [start, end]
  const incomingBefore = incoming.at(-2)!
  const outgoingAfter = outgoing[1]
  const incomingLength = Math.max(1e-6, Math.hypot(
    start[0] - incomingBefore[0],
    start[1] - incomingBefore[1],
  ))
  const outgoingLength = Math.max(1e-6, Math.hypot(
    outgoingAfter[0] - end[0],
    outgoingAfter[1] - end[1],
  ))
  const incomingDirection: [number, number] = [
    (start[0] - incomingBefore[0]) / incomingLength,
    (start[1] - incomingBefore[1]) / incomingLength,
  ]
  const outgoingDirection: [number, number] = [
    (outgoingAfter[0] - end[0]) / outgoingLength,
    (outgoingAfter[1] - end[1]) / outgoingLength,
  ]
  const outgoingLeadLength = Math.min(3, gap * 0.65)
  const incomingLeadLength = Math.min(2, gap * 0.2)
  const curveStart: [number, number] = [
    start[0] + incomingDirection[0] * incomingLeadLength,
    start[1] + incomingDirection[1] * incomingLeadLength,
  ]
  const curveEnd: [number, number] = [
    end[0] - outgoingDirection[0] * outgoingLeadLength,
    end[1] - outgoingDirection[1] * outgoingLeadLength,
  ]
  const curveGap = Math.hypot(curveEnd[0] - curveStart[0], curveEnd[1] - curveStart[1])
  const controlLength = curveGap * 0.25
  const firstControl: [number, number] = [
    curveStart[0] + incomingDirection[0] * controlLength,
    curveStart[1] + incomingDirection[1] * controlLength,
  ]
  const secondControl: [number, number] = [
    curveEnd[0] - outgoingDirection[0] * controlLength,
    curveEnd[1] - outgoingDirection[1] * controlLength,
  ]
  const curve = Array.from({ length: 17 }, (_, index) => {
    const t = index / 16
    const inverse = 1 - t
    return [
      inverse ** 3 * curveStart[0]
        + 3 * inverse ** 2 * t * firstControl[0]
        + 3 * inverse * t ** 2 * secondControl[0]
        + t ** 3 * curveEnd[0],
      inverse ** 3 * curveStart[1]
        + 3 * inverse ** 2 * t * firstControl[1]
        + 3 * inverse * t ** 2 * secondControl[1]
        + t ** 3 * curveEnd[1],
    ] as [number, number]
  })
  return [start, ...curve, end]
}

function laneHeadingAtPosition(lane: RealisticLane, lanePosition?: number): number | null {
  if (lanePosition == null || !Number.isFinite(lanePosition)) return null
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
  const horizontalScale = manifest.horizontalScale ?? 1
  const stopBoundaries = new Map<string, number>()
  for (const { geometry } of buildCollisionFreeIntersectionApproaches(
    manifest.edges,
    horizontalScale,
  )) {
    for (const sample of geometry.laneSamples) {
      const points = visualLanePoints(sample.lane)
      const nearest = nearestPolylineProgress(sample.point, points)
      if (!nearest) continue
      stopBoundaries.set(sample.lane.id, nearest.progress * polylineLength(points))
    }
  }
  for (const edge of manifest.edges.filter((candidate) => candidate.incoming)) {
    const fallback = buildIntersectionApproachGeometry(
      edge,
      horizontalScale,
      manifest.edges,
    )
    if (!fallback) continue
    for (const sample of fallback.laneSamples) {
      if (stopBoundaries.has(sample.lane.id)) continue
      const points = visualLanePoints(sample.lane)
      const nearest = nearestPolylineProgress(sample.point, points)
      if (!nearest) continue
      stopBoundaries.set(sample.lane.id, nearest.progress * polylineLength(points))
    }
  }
  const tracks = new Map<string, LaneTrack[]>()
  const tracksByKey = new Map<string, LaneTrack>()
  const motionPaths = new Map<string, MotionPathGeometry>()
  const laneMetadata = new Map<string, { edgeId: string; laneIndex: number }>()
  const addTrack = (track: LaneTrack) => {
    const candidates = tracks.get(track.laneId) ?? []
    candidates.push(track)
    tracks.set(track.laneId, candidates)
    tracksByKey.set(track.key, track)
  }
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      laneMetadata.set(lane.id, { edgeId: edge.id, laneIndex: lane.index })
      if (!lane.renderPoints?.length) continue
      addTrack({
        key: `lane:${lane.id}`,
        motionPathKey: `lane:${lane.id}`,
        segmentKey: `lane:${lane.id}`,
        occupancyKey: `lane:${lane.id}`,
        laneId: lane.id,
        edgeId: edge.id,
        laneIndex: lane.index,
        sourcePoints: lane.points,
        renderPoints: lane.renderPoints,
        routeLaneIds: [lane.id],
        stopBoundaryDistance: edge.incoming ? stopBoundaries.get(lane.id) : undefined,
      })
      motionPaths.set(`lane:${lane.id}`, {
        points: lane.renderPoints,
        lengthSceneUnits: polylineLength(lane.renderPoints),
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
    if (!fromLane?.renderPoints?.length || !toLane?.renderPoints?.length) continue
    const routeKey = `${connection.tlsId}:${connection.linkIndex}`
    const motionPathKey = `route:${routeKey}`
    const routeLaneIds = [fromLane.id, ...segments.map((segment) => segment.laneId), toLane.id]
    const directConnector = segments.length === 0
      ? connectorPolyline(fromLane.renderPoints, toLane.renderPoints)
      : []
    const routeRenderPoints = joinPolylines(
      fromLane.renderPoints,
      directConnector,
      ...segments.map((segment) => segment.renderPoints),
      toLane.renderPoints,
    )
    motionPaths.set(motionPathKey, {
      points: routeRenderPoints,
      lengthSceneUnits: polylineLength(routeRenderPoints),
    })
    let beforeRenderPoints = fromLane.renderPoints
    for (const [segmentIndex, segment] of segments.entries()) {
      laneMetadata.set(segment.laneId, {
        edgeId: connection.fromEdge,
        laneIndex: connection.fromLane,
      })
      addTrack({
        key: `${routeKey}:${segment.laneId}`,
        motionPathKey,
        segmentKey: `route:${routeKey}:${segment.laneId}`,
        occupancyKey: `lane:${segment.laneId}`,
        laneId: segment.laneId,
        edgeId: connection.fromEdge,
        laneIndex: connection.fromLane,
        sourcePoints: segment.points,
        renderPoints: segment.renderPoints,
        beforeRenderPoints,
        routeKey,
        routeLaneIds,
        routeSegmentIndex: segmentIndex + 1,
      })
      beforeRenderPoints = joinPolylines(beforeRenderPoints, segment.renderPoints)
    }
    if (segments.length === 0) {
      beforeRenderPoints = joinPolylines(beforeRenderPoints, directConnector)
    }
    addTrack({
      key: `${routeKey}:${toLane.id}`,
      motionPathKey,
      segmentKey: `route:${routeKey}:${toLane.id}`,
      occupancyKey: `lane:${toLane.id}`,
      laneId: toLane.id,
      edgeId: connection.toEdge,
      laneIndex: connection.toLane,
      sourcePoints: toLane.points,
      renderPoints: toLane.renderPoints,
      beforeRenderPoints,
      routeKey,
      routeLaneIds,
      routeSegmentIndex: routeLaneIds.length - 1,
    })
  }
  const projectedOrigin = projector([
    manifest.origin.longitude,
    manifest.origin.latitude,
    0,
  ])
  const originPlane = projectBd09ToWebMercator([projectedOrigin[0], projectedOrigin[1]])
  const motionPathSampler: MotionPathSampler = {
    project(motionPathKey, coordinate) {
      const path = motionPaths.get(motionPathKey)
      if (!path || path.lengthSceneUnits <= 1e-6) return null
      const projected = projectBd09ToWebMercator(coordinate)
      const nearest = nearestPolylineProgress([
        projected[0] - originPlane[0],
        projected[1] - originPlane[1],
      ], path.points)
      if (!nearest) return null
      return {
        pathArcDistanceMeters: nearest.progress * path.lengthSceneUnits / horizontalScale,
        distanceMeters: nearest.distance / horizontalScale,
      }
    },
    sample(motionPathKey, pathArcDistanceMeters) {
      const path = motionPaths.get(motionPathKey)
      if (!path || path.lengthSceneUnits <= 1e-6) return null
      const distanceSceneUnits = Math.max(
        0,
        Math.min(path.lengthSceneUnits, pathArcDistanceMeters * horizontalScale),
      )
      const progress = distanceSceneUnits / path.lengthSceneUnits
      const point = samplePolyline(path.points, progress)
      const heading = tangentAtProgress(path.points, progress)
      if (heading == null) return null
      const [longitude, latitude] = unprojectWebMercatorToBd09([
        originPlane[0] + point[0],
        originPlane[1] + point[1],
      ])
      return {
        longitude,
        latitude,
        heading,
        pathArcDistanceMeters: distanceSceneUnits / horizontalScale,
      }
    },
  }
  const resolver = ((
    laneId: string,
    coordinate: readonly [number, number],
    modelCenterOffsetMeters = 0,
    previousLaneId?: string,
    previousTrackKey?: string,
    options?: LanePoseRuntimeOptions,
  ): ResolvedLanePose | null => {
    const candidates = tracks.get(laneId)
    if (!candidates?.length) return null
    const projected = projectBd09ToWebMercator(coordinate)
    const local: [number, number] = [
      projected[0] - originPlane[0],
      projected[1] - originPlane[1],
    ]
    const previousTrack = previousTrackKey ? tracksByKey.get(previousTrackKey) : undefined
    const currentMetadata = laneMetadata.get(laneId)
    const previousMetadata = previousLaneId ? laneMetadata.get(previousLaneId) : undefined
    const laneChanging = Boolean(
      previousLaneId
      && previousLaneId !== laneId
      && currentMetadata
      && previousMetadata
      && currentMetadata.edgeId === previousMetadata.edgeId
      && Math.abs(currentMetadata.laneIndex - previousMetadata.laneIndex) === 1,
    )
    const topologicalCandidates = previousLaneId
      ? candidates.filter((candidate) => {
          const previousIndex = candidate.routeLaneIds.indexOf(previousLaneId)
          const currentIndex = candidate.routeLaneIds.indexOf(laneId)
          return previousIndex >= 0 && currentIndex === previousIndex + 1
        })
      : []
    const sameTrackCandidates = previousTrack
      ? candidates.filter((candidate) => candidate.key === previousTrack.key)
      : []
    const laneChangeCandidates = laneChanging
      ? candidates.filter((candidate) => candidate.key === `lane:${laneId}`)
      : []
    const initialCandidates = previousLaneId
      ? []
      : candidates.filter((candidate) => candidate.key === `lane:${laneId}`)
    if (!previousLaneId && initialCandidates.length === 0 && candidates.length === 1) {
      initialCandidates.push(candidates[0])
    }
    const candidatePool = sameTrackCandidates.length
      ? sameTrackCandidates
      : topologicalCandidates.length
        ? topologicalCandidates
        : laneChangeCandidates.length
          ? laneChangeCandidates
          : initialCandidates.length
            ? initialCandidates
            : options?.relaxedTrackContinuity === true && previousLaneId === laneId
              ? candidates.filter((candidate) => candidate.key === `lane:${laneId}`)
              : []
    if (candidatePool.length === 0) return null
    const transitionKind: LanePoseTransitionKind = !previousLaneId
      ? 'initial'
      : previousLaneId === laneId
        ? 'same_lane'
        : laneChanging
          ? 'lane_change'
          : 'topological'
    const moving = (options?.speedMetersPerSecond ?? 0) > 0.35
    const maximumHeadingDelta = moving
      ? MAX_MOTION_PATH_HEADING_DELTA
      : 60 * Math.PI / 180
    const resolvedCandidates = candidatePool
      .map((track) => {
        const nearest = nearestPolylineProgress(local, track.sourcePoints)
        const heading = nearest ? tangentAtProgress(track.sourcePoints, nearest.progress) : null
        const headingDelta = heading != null && options?.expectedHeading != null
          ? Math.abs(shortestAngleDelta(heading, options.expectedHeading))
          : 0
        const headingPenalty = headingDelta * 4 * horizontalScale
        const headingCompatible = options?.expectedHeading == null || heading == null
          || headingDelta <= maximumHeadingDelta
        const progressPenalty = nearest
          && previousTrackKey === track.key
          && options?.previousTrackProgress != null
          && nearest.progress + 0.04 < options.previousTrackProgress
          ? (options.previousTrackProgress - nearest.progress) * 20 * horizontalScale
          : 0
        return {
          track,
          nearest,
          headingDelta,
          headingCompatible,
          score: (nearest?.distance ?? Number.POSITIVE_INFINITY) + headingPenalty + progressPenalty,
        }
      })
      .filter((candidate) => candidate.nearest !== null && candidate.headingCompatible)
      .sort((left, right) => left.score - right.score)
    const resolved = resolvedCandidates[0]
    const maximumSnapDistance = (options?.maximumSnapDistanceMeters ?? 4) * horizontalScale
    if (!resolved || resolved.nearest!.distance > maximumSnapDistance) return null
    const { track } = resolved
    const nearest = resolved.nearest!
    const sourceLength = polylineLength(track.sourcePoints)
    const renderLength = polylineLength(track.renderPoints)
    const backtrackSceneUnits = Math.max(0, modelCenterOffsetMeters) * horizontalScale
    const mappedFrontDistance = mapSourceProgressToRenderDistance(
      nearest.progress,
      sourceLength,
      renderLength,
    )
    let targetDistance = mappedFrontDistance - backtrackSceneUnits
    let stopClamped = false
    const stopFrontLimitDistance = track.stopBoundaryDistance == null
      ? undefined
      : visualStopFrontLimitDistance(track.stopBoundaryDistance, horizontalScale)
    if (
      track.stopBoundaryDistance != null
      && options?.allowStopClamp !== false
      && (options?.speedMetersPerSecond ?? Number.POSITIVE_INFINITY) <= 0.35
      && options?.laneRuntime?.role !== 'outgoing'
      && options?.laneRuntime?.role !== 'internal'
    ) {
      const runtime = options?.laneRuntime
      const signalState = runtime?.signal_state?.toLowerCase() ?? ''
      const explicitStop = runtime?.lane_has_green === false
        || (runtime?.lane_has_green !== true && /^[ry]+$/.test(signalState))
      const lacksSignalDetail = !runtime
        || (runtime.lane_has_green == null && !signalState)
      const maximumFrontDistance = stopFrontLimitDistance!
      const overrunMeters = (mappedFrontDistance - maximumFrontDistance) / horizontalScale
      const conservativeFallback = lacksSignalDetail
        && (options?.speedMetersPerSecond ?? Number.POSITIVE_INFINITY) <= 0.05
        && overrunMeters > 0
        && overrunMeters <= 1.5
      if (mappedFrontDistance > maximumFrontDistance && (explicitStop || conservativeFallback)) {
        targetDistance = maximumFrontDistance - backtrackSceneUnits
        stopClamped = true
      }
    }
    if (
      previousTrackKey === track.key
      && Number.isFinite(options?.minimumModelCenterDistanceMeters)
    ) {
      targetDistance = Math.max(
        targetDistance,
        Number(options?.minimumModelCenterDistanceMeters) * horizontalScale,
      )
    }
    if (Number.isFinite(options?.maximumModelCenterDistanceMeters)) {
      targetDistance = Math.min(
        targetDistance,
        Number(options?.maximumModelCenterDistanceMeters) * horizontalScale,
      )
    }
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
    const pathArcDistanceMeters = track.routeKey
      ? (polylineLength(track.beforeRenderPoints ?? []) + targetDistance) / horizontalScale
      : targetDistance / horizontalScale
    const pathSample = track.routeKey
      ? motionPathSampler.sample(track.motionPathKey, pathArcDistanceMeters)
      : null
    const pathHeading = pathSample?.heading ?? tangentAtProgress(activePoints, renderProgress)
    if (pathHeading == null) return null
    const renderedHeadingDelta = options?.expectedHeading == null
      ? 0
      : Math.abs(shortestAngleDelta(pathHeading, options.expectedHeading))
    if (
      moving
      && options?.expectedHeading != null
      && renderedHeadingDelta > maximumHeadingDelta
    ) return null
    const heading = pathHeading
    const [longitude, latitude] = pathSample
      ? [pathSample.longitude, pathSample.latitude]
      : unprojectWebMercatorToBd09([
          originPlane[0] + renderPoint[0],
          originPlane[1] + renderPoint[1],
        ])
    const pathMinimumArcDistanceMeters = track.beforeRenderPoints?.length
      ? -polylineLength(track.beforeRenderPoints) / horizontalScale
      : 0
    const isRouteExit = track.routeKey && track.routeLaneIds.at(-1) === track.laneId
    const rearEnteredLane = targetDistance >= backtrackSceneUnits
    const occupancyKey = isRouteExit && !rearEnteredLane
      ? `path:${track.motionPathKey}:${track.segmentKey}`
      : track.occupancyKey
    const minimumArcDistanceMeters = rearEnteredLane ? 0 : pathMinimumArcDistanceMeters
    const normalizedDistance = resolved.nearest!.distance / Math.max(maximumSnapDistance, 1e-6)
    const normalizedHeading = Math.max(resolved.headingDelta, renderedHeadingDelta)
      / Math.max(maximumHeadingDelta, 1e-6)
    return {
      longitude,
      latitude,
      heading,
      modelCenterResolved: modelCenterOffsetMeters > 0,
      trackKey: track.key,
      motionPathKey: track.motionPathKey,
      segmentKey: track.segmentKey,
      occupancyKey,
      trackProgress: nearest.progress,
      arcDistanceMeters: targetDistance / horizontalScale,
      pathArcDistanceMeters,
      minimumArcDistanceMeters,
      matchConfidence: Math.max(0, 1 - normalizedDistance * 0.65 - normalizedHeading * 0.35),
      transitionKind,
      modelCenterDistanceMeters: targetDistance / horizontalScale,
      naturalFrontDistanceMeters: mappedFrontDistance / horizontalScale,
      stopFrontLimitDistanceMeters: stopFrontLimitDistance == null
        ? undefined
        : stopFrontLimitDistance / horizontalScale,
      stopClamped,
    }
  }) as LanePoseResolver
  resolver.motionPathSampler = motionPathSampler
  return resolver
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
