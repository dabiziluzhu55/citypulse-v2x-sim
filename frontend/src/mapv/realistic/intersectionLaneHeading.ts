import type { RealisticIntersectionManifest, RealisticLane } from './intersectionManifest.ts'
import type { RoadCoordinateProjector } from '../roadGeometry.ts'
import { shortestAngleDelta } from '../vehicleOrientation.ts'
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
  sourceArcDistanceMeters: number
  sourceLateralOffsetMeters: number
  sourceDistanceToLaneCenterMeters: number
  mappedArcDistanceMeters: number
  roadMappingErrorMeters: number
  laneWidthMeters: number
  corridorKind: 'lane' | 'lane_change_union' | 'junction'
  poseValid: boolean
  mappingMode: 'centerline' | 'source_lateral'
}
export interface LanePoseRuntimeOptions {
  speedMetersPerSecond?: number
  expectedHeading?: number | null
  previousTrackProgress?: number
  maximumSnapDistanceMeters?: number
  relaxedTrackContinuity?: boolean
  preserveSourceLateralOffset?: boolean
  preferredMotionPathKey?: string
  requirePreferredMotionPath?: boolean
  routeHintRejected?: boolean
  vehicleHalfWidthMeters?: number
  vehicleHalfLengthMeters?: number
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
  hasLane(laneId: string): boolean
  covers(laneId: string, coordinate: readonly [number, number]): boolean
  coversDetailedArea(coordinate: readonly [number, number]): boolean
}

export const VISUAL_STOP_BOUNDARY_CLEARANCE_METERS = 0.25
export const VISUAL_STOP_LINE_HALF_WIDTH_METERS = 0.21
export const MAX_MOTION_PATH_HEADING_DELTA = 35 * Math.PI / 180

export function visualStopFrontLimitDistance(
  stopBoundaryDistanceMeters: number,
  _horizontalScale = 1,
): number {
  return stopBoundaryDistanceMeters
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
  sourceStationsMeters?: number[]
  sourceStartMeters?: number
  sourceEndMeters?: number
  beforeRenderPoints?: [number, number][]
  routeKey?: string
  routeLaneIds: string[]
  routeSegmentIndex?: number
  stopBoundaryDistance?: number
  widthMeters: number
  sourceRenderStartDistanceSceneUnits: number
  sourceRenderEndDistanceSceneUnits: number
  pathSourceStartMeters: number
}

interface MotionPathGeometry {
  segments: Array<{
    renderPoints: [number, number][]
    sourceStationsMeters?: number[]
    sourceStartMeters: number
    sourceLengthMeters: number
  }>
  lengthMeters: number
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
  return x * renderLength
}

function sampleGuideAtSourceDistance(
  points: [number, number][],
  sourceStationsMeters: number[] | undefined,
  distanceMeters: number,
): { point: [number, number]; progress: number; heading: number | null } | null {
  if (!sourceStationsMeters || sourceStationsMeters.length !== points.length || points.length < 2) return null
  const target = Math.max(0, Math.min(sourceStationsMeters.at(-1) ?? 0, distanceMeters))
  for (let index = 1; index < sourceStationsMeters.length; index += 1) {
    if (target > sourceStationsMeters[index] && index < sourceStationsMeters.length - 1) continue
    const span = sourceStationsMeters[index] - sourceStationsMeters[index - 1]
    const ratio = span > 1e-9 ? (target - sourceStationsMeters[index - 1]) / span : 0
    const previous = points[index - 1]
    const current = points[index]
    const dx = current[0] - previous[0]
    const dy = current[1] - previous[1]
    const total = sourceStationsMeters.at(-1) ?? 0
    return {
      point: [previous[0] + dx * ratio, previous[1] + dy * ratio],
      progress: total > 1e-9 ? target / total : 0,
      heading: Math.hypot(dx, dy) > 1e-6 ? Math.atan2(dy, dx) : null,
    }
  }
  return null
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
  manifest: Pick<
    RealisticIntersectionManifest,
    'edges' | 'connections' | 'vehicleConnections' | 'origin' | 'horizontalScale'
    | 'junctionShape' | 'radiusMeters' | 'radiusSceneUnits'
  >,
  projector: RoadCoordinateProjector,
): LanePoseResolver {
  const horizontalScale = manifest.horizontalScale ?? 1
  const stopBoundaries = new Map<string, number>()
  for (const edge of manifest.edges.filter((candidate) => candidate.incoming)) {
    for (const lane of edge.lanes) {
      const points = lane.vehicleGuidePoints?.length ? lane.vehicleGuidePoints : visualLanePoints(lane)
      const sourceLengthMeters = lane.vehicleGuideSourceStationsMeters?.at(-1)
        ?? polylineLength(points) / horizontalScale
      if (points.length >= 2) stopBoundaries.set(lane.id, sourceLengthMeters)
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
  const createTrack = (
    track: Omit<
      LaneTrack,
      'sourceRenderStartDistanceSceneUnits' | 'sourceRenderEndDistanceSceneUnits' | 'pathSourceStartMeters'
    > & { pathSourceStartMeters?: number },
  ): LaneTrack => {
    const sourceLength = polylineLength(track.sourcePoints)
    if (track.laneId.startsWith(':')) {
      return {
        ...track,
        pathSourceStartMeters: track.pathSourceStartMeters ?? 0,
        sourceRenderStartDistanceSceneUnits: 0,
        sourceRenderEndDistanceSceneUnits: sourceLength,
      }
    }
    const startProjection = nearestPolylineProgress(track.renderPoints[0], track.sourcePoints)
    const endProjection = nearestPolylineProgress(track.renderPoints.at(-1)!, track.sourcePoints)
    const startDistance = track.sourceStartMeters != null
      ? track.sourceStartMeters * horizontalScale
      : (startProjection?.progress ?? 0) * sourceLength
    const endDistance = track.sourceEndMeters != null
      ? track.sourceEndMeters * horizontalScale
      : (endProjection?.progress ?? 1) * sourceLength
    return {
      ...track,
      pathSourceStartMeters: track.pathSourceStartMeters ?? 0,
      sourceRenderStartDistanceSceneUnits: Math.min(startDistance, endDistance),
      sourceRenderEndDistanceSceneUnits: Math.max(startDistance, endDistance),
    }
  }
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      laneMetadata.set(lane.id, { edgeId: edge.id, laneIndex: lane.index })
      if (!lane.renderPoints?.length) continue
      const vehicleGuidePoints = lane.vehicleGuidePoints?.length
        ? lane.vehicleGuidePoints
        : lane.renderPoints
      const laneTrack = createTrack({
        key: `lane:${lane.id}`,
        motionPathKey: `lane:${lane.id}`,
        segmentKey: `lane:${lane.id}`,
        occupancyKey: `lane:${lane.id}`,
        laneId: lane.id,
        edgeId: edge.id,
        laneIndex: lane.index,
        sourcePoints: lane.points,
        renderPoints: vehicleGuidePoints,
        sourceStationsMeters: lane.vehicleGuideSourceStationsMeters,
        sourceStartMeters: lane.vehicleGuideSourceStartMeters,
        sourceEndMeters: lane.vehicleGuideSourceEndMeters,
        routeLaneIds: [lane.id],
        stopBoundaryDistance: edge.incoming ? stopBoundaries.get(lane.id) : undefined,
        widthMeters: lane.widthMeters ?? lane.width / horizontalScale,
      })
      addTrack(laneTrack)
      motionPaths.set(`lane:${lane.id}`, {
        segments: [{
          renderPoints: vehicleGuidePoints,
          sourceStationsMeters: lane.vehicleGuideSourceStationsMeters,
          sourceStartMeters: 0,
          sourceLengthMeters: (
            laneTrack.sourceRenderEndDistanceSceneUnits
            - laneTrack.sourceRenderStartDistanceSceneUnits
          ) / horizontalScale,
        }],
        lengthMeters: (
          laneTrack.sourceRenderEndDistanceSceneUnits
          - laneTrack.sourceRenderStartDistanceSceneUnits
        ) / horizontalScale,
      })
    }
  }
  for (const connection of manifest.vehicleConnections ?? manifest.connections) {
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
            vehicleGuidePoints: connection.vehicleGuidePoints,
            vehicleGuideSourceStationsMeters: connection.vehicleGuideSourceStationsMeters,
            vehicleSourcePoints: connection.vehicleSourcePoints,
          }]
        : []
    if (!fromLane?.renderPoints?.length || !toLane?.renderPoints?.length) continue
    const routeKey = `${connection.tlsId}:${connection.linkIndex}`
    const motionPathKey = `route:${routeKey}`
    const routeLaneIds = [fromLane.id, ...segments.map((segment) => segment.laneId), toLane.id]
    const directConnector = segments.length === 0
      ? connectorPolyline(fromLane.renderPoints, toLane.renderPoints)
      : []
    const fromGuidePoints = fromLane.vehicleGuidePoints?.length
      ? fromLane.vehicleGuidePoints
      : fromLane.renderPoints
    const toGuidePoints = toLane.vehicleGuidePoints?.length
      ? toLane.vehicleGuidePoints
      : toLane.renderPoints
    const routeTracks: LaneTrack[] = []
    const fromTrack = createTrack({
      key: `${routeKey}:${fromLane.id}`,
      motionPathKey,
      segmentKey: `route:${routeKey}:${fromLane.id}`,
      occupancyKey: `lane:${fromLane.id}`,
      laneId: fromLane.id,
      edgeId: connection.fromEdge,
      laneIndex: connection.fromLane,
      sourcePoints: fromLane.points,
      renderPoints: fromGuidePoints,
      sourceStationsMeters: fromLane.vehicleGuideSourceStationsMeters,
      sourceStartMeters: fromLane.vehicleGuideSourceStartMeters,
      sourceEndMeters: fromLane.vehicleGuideSourceEndMeters,
      routeKey,
      routeLaneIds,
      routeSegmentIndex: 0,
      stopBoundaryDistance: stopBoundaries.get(fromLane.id),
      widthMeters: fromLane.widthMeters ?? fromLane.width / horizontalScale,
    })
    routeTracks.push(fromTrack)
    let beforeRenderPoints = fromGuidePoints
    for (const [segmentIndex, segment] of segments.entries()) {
      laneMetadata.set(segment.laneId, {
        edgeId: connection.fromEdge,
        laneIndex: connection.fromLane,
      })
      const segmentTrack = createTrack({
        key: `${routeKey}:${segment.laneId}`,
        motionPathKey,
        segmentKey: `route:${routeKey}:${segment.laneId}`,
        occupancyKey: `lane:${segment.laneId}`,
        laneId: segment.laneId,
        edgeId: connection.fromEdge,
        laneIndex: connection.fromLane,
        sourcePoints: segment.vehicleSourcePoints?.length
          ? segment.vehicleSourcePoints
          : segment.points,
        renderPoints: segment.vehicleGuidePoints?.length
          ? segment.vehicleGuidePoints
          : segment.renderPoints,
        sourceStationsMeters: segment.vehicleGuideSourceStationsMeters,
        beforeRenderPoints,
        routeKey,
        routeLaneIds,
        routeSegmentIndex: segmentIndex + 1,
        widthMeters: fromLane.widthMeters ?? fromLane.width / horizontalScale,
      })
      routeTracks.push(segmentTrack)
      beforeRenderPoints = joinPolylines(
        beforeRenderPoints,
        segment.vehicleGuidePoints?.length ? segment.vehicleGuidePoints : segment.renderPoints,
      )
    }
    if (segments.length === 0) {
      beforeRenderPoints = joinPolylines(beforeRenderPoints, directConnector)
    }
    const toTrack = createTrack({
      key: `${routeKey}:${toLane.id}`,
      motionPathKey,
      segmentKey: `route:${routeKey}:${toLane.id}`,
      occupancyKey: `lane:${toLane.id}`,
      laneId: toLane.id,
      edgeId: connection.toEdge,
      laneIndex: connection.toLane,
      sourcePoints: toLane.points,
      renderPoints: toGuidePoints,
      sourceStationsMeters: toLane.vehicleGuideSourceStationsMeters,
      sourceStartMeters: toLane.vehicleGuideSourceStartMeters,
      sourceEndMeters: toLane.vehicleGuideSourceEndMeters,
      beforeRenderPoints,
      routeKey,
      routeLaneIds,
      routeSegmentIndex: routeLaneIds.length - 1,
      widthMeters: toLane.widthMeters ?? toLane.width / horizontalScale,
    })
    routeTracks.push(toTrack)
    const routeSegments: MotionPathGeometry['segments'] = []
    let sourceStartMeters = 0
    for (const [trackIndex, track] of routeTracks.entries()) {
      track.pathSourceStartMeters = sourceStartMeters
      const sourceLengthMeters = Math.max(
        0,
        track.sourceRenderEndDistanceSceneUnits - track.sourceRenderStartDistanceSceneUnits,
      ) / horizontalScale
      routeSegments.push({
        renderPoints: track.renderPoints,
        sourceStationsMeters: track.sourceStationsMeters,
        sourceStartMeters,
        sourceLengthMeters,
      })
      sourceStartMeters += sourceLengthMeters
      if (segments.length === 0 && trackIndex === 0 && directConnector.length >= 2) {
        const connectorLengthMeters = polylineLength(directConnector) / horizontalScale
        routeSegments.push({
          renderPoints: directConnector,
          sourceStartMeters,
          sourceLengthMeters: connectorLengthMeters,
        })
        sourceStartMeters += connectorLengthMeters
      }
    }
    for (const track of routeTracks) addTrack(track)
    motionPaths.set(motionPathKey, {
      segments: routeSegments,
      lengthMeters: sourceStartMeters,
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
      if (!path || path.lengthMeters <= 1e-6) return null
      const projected = projectBd09ToWebMercator(coordinate)
      const localPoint: [number, number] = [
        projected[0] - originPlane[0],
        projected[1] - originPlane[1],
      ]
      const nearest = path.segments
        .map((segment) => ({ segment, nearest: nearestPolylineProgress(localPoint, segment.renderPoints) }))
        .filter((entry) => entry.nearest !== null)
        .sort((left, right) => left.nearest!.distance - right.nearest!.distance)[0]
      if (!nearest?.nearest) return null
      return {
        pathArcDistanceMeters: nearest.segment.sourceStartMeters
          + nearest.nearest.progress * nearest.segment.sourceLengthMeters,
        distanceMeters: nearest.nearest.distance / horizontalScale,
      }
    },
    sample(motionPathKey, pathArcDistanceMeters) {
      const path = motionPaths.get(motionPathKey)
      if (!path || path.lengthMeters <= 1e-6) return null
      const distanceMeters = Math.max(0, Math.min(path.lengthMeters, pathArcDistanceMeters))
      const segment = path.segments.find((candidate, index) => (
        distanceMeters <= candidate.sourceStartMeters + candidate.sourceLengthMeters + 1e-9
        || index === path.segments.length - 1
      ))
      if (!segment) return null
      const progress = segment.sourceLengthMeters > 1e-9
        ? Math.max(0, Math.min(1, (
            distanceMeters - segment.sourceStartMeters
          ) / segment.sourceLengthMeters))
        : 0
      const guideSample = sampleGuideAtSourceDistance(
        segment.renderPoints,
        segment.sourceStationsMeters,
        distanceMeters - segment.sourceStartMeters,
      )
      const point = guideSample?.point ?? samplePolyline(segment.renderPoints, progress)
      const heading = guideSample?.heading ?? tangentAtProgress(segment.renderPoints, progress)
      if (heading == null) return null
      const [longitude, latitude] = unprojectWebMercatorToBd09([
        originPlane[0] + point[0],
        originPlane[1] + point[1],
      ])
      return {
        longitude,
        latitude,
        heading,
        pathArcDistanceMeters: distanceMeters,
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
    const preferredCandidates = options?.preferredMotionPathKey
      ? candidates.filter((candidate) => candidate.motionPathKey === options.preferredMotionPathKey)
      : []
    if (options?.preferredMotionPathKey && options.requirePreferredMotionPath && preferredCandidates.length === 0) {
      return null
    }
    const failClosedCandidates = options?.routeHintRejected
      ? laneId.startsWith(':')
        ? []
        : candidates.filter((candidate) => candidate.key === `lane:${laneId}`)
      : null
    const candidatePool = failClosedCandidates
      ?? (preferredCandidates.length
      ? preferredCandidates
      : sameTrackCandidates.length
      ? sameTrackCandidates
      : topologicalCandidates.length
        ? topologicalCandidates
        : laneChangeCandidates.length
          ? laneChangeCandidates
          : initialCandidates.length
            ? initialCandidates
            : options?.relaxedTrackContinuity === true && previousLaneId === laneId
              ? candidates.filter((candidate) => candidate.key === `lane:${laneId}`)
              : [])
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
    const sourcePoint = samplePolyline(track.sourcePoints, nearest.progress)
    const sourceHeading = tangentAtProgress(track.sourcePoints, nearest.progress)
    if (sourceHeading == null) return null
    const sourceNormal: [number, number] = [-Math.sin(sourceHeading), Math.cos(sourceHeading)]
    const sourceLateralOffsetScene = (
      (local[0] - sourcePoint[0]) * sourceNormal[0]
      + (local[1] - sourcePoint[1]) * sourceNormal[1]
    )
    const appliedLateralOffsetScene = options?.preserveSourceLateralOffset === true
      ? sourceLateralOffsetScene
      : 0
    const vehicleHalfWidthMeters = Math.max(0, options?.vehicleHalfWidthMeters ?? 0)
    const vehicleHalfLengthMeters = Math.max(0, options?.vehicleHalfLengthMeters ?? 0)
    const maximumCenterOffsetMeters = Math.max(0, track.widthMeters / 2 - vehicleHalfWidthMeters)
    let corridorMinimumOffsetMeters = -maximumCenterOffsetMeters
    let corridorMaximumOffsetMeters = maximumCenterOffsetMeters
    if (laneChanging && previousTrack) {
      const previousNearest = nearestPolylineProgress(sourcePoint, previousTrack.sourcePoints)
      const previousCenterPoint = previousNearest
        ? samplePolyline(previousTrack.sourcePoints, previousNearest.progress)
        : null
      if (previousCenterPoint) {
        const previousCenterOffsetMeters = (
          (previousCenterPoint[0] - sourcePoint[0]) * sourceNormal[0]
          + (previousCenterPoint[1] - sourcePoint[1]) * sourceNormal[1]
        ) / horizontalScale
        const previousHalfWidthMeters = previousTrack.widthMeters / 2
        corridorMinimumOffsetMeters = Math.min(
          -track.widthMeters / 2,
          previousCenterOffsetMeters - previousHalfWidthMeters,
        ) + vehicleHalfWidthMeters
        corridorMaximumOffsetMeters = Math.max(
          track.widthMeters / 2,
          previousCenterOffsetMeters + previousHalfWidthMeters,
        ) - vehicleHalfWidthMeters
      }
    }
    const sourceLateralOffsetMeters = sourceLateralOffsetScene / horizontalScale
    if (
      options?.preserveSourceLateralOffset === true
      && options.vehicleHalfWidthMeters != null
      && (
        sourceLateralOffsetMeters < corridorMinimumOffsetMeters - 0.15
        || sourceLateralOffsetMeters > corridorMaximumOffsetMeters + 0.15
      )
    ) return null
    const backtrackSceneUnits = Math.max(0, modelCenterOffsetMeters) * horizontalScale
    const sourceFrontDistance = nearest.progress * sourceLength
    const sourceRenderSpan = Math.max(
      1e-6,
      track.sourceRenderEndDistanceSceneUnits - track.sourceRenderStartDistanceSceneUnits,
    )
    if (
      sourceFrontDistance < track.sourceRenderStartDistanceSceneUnits - maximumSnapDistance
      || sourceFrontDistance > track.sourceRenderEndDistanceSceneUnits + maximumSnapDistance
    ) return null
    const sourceRenderProgress = Math.max(0, Math.min(
      1,
      (sourceFrontDistance - track.sourceRenderStartDistanceSceneUnits) / sourceRenderSpan,
    ))
    const sourceDistanceOnGuideMeters = sourceRenderProgress * sourceRenderSpan / horizontalScale
    const guideSample = sampleGuideAtSourceDistance(
      track.renderPoints,
      track.sourceStationsMeters,
      sourceDistanceOnGuideMeters,
    )
    const mappedFrontDistance = (guideSample?.progress ?? sourceRenderProgress) * renderLength
    const targetDistance = mappedFrontDistance - backtrackSceneUnits
    const stopFrontLimitDistance = track.stopBoundaryDistance == null
      ? undefined
      : visualStopFrontLimitDistance(track.stopBoundaryDistance)
    let activePoints = track.renderPoints
    let renderProgress = renderLength > 1e-6 ? Math.max(0, targetDistance / renderLength) : nearest.progress
    if (targetDistance < 0 && track.beforeRenderPoints?.length) {
      const beforeLength = polylineLength(track.beforeRenderPoints)
      activePoints = track.beforeRenderPoints
      renderProgress = beforeLength > 1e-6
        ? Math.max(0, 1 + targetDistance / beforeLength)
        : 1
    }
    const renderPoint = targetDistance >= 0 && guideSample
      ? guideSample.point
      : samplePolyline(activePoints, renderProgress)
    const sourceCenterDistanceMeters = (
      sourceFrontDistance - track.sourceRenderStartDistanceSceneUnits
    ) / horizontalScale - Math.max(0, modelCenterOffsetMeters)
    const pathArcDistanceMeters = track.pathSourceStartMeters + sourceCenterDistanceMeters
    const pathSample = motionPathSampler.sample(track.motionPathKey, pathArcDistanceMeters)
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
    const pathPoint = pathSample
      ? projectBd09ToWebMercator([pathSample.longitude, pathSample.latitude])
      : [originPlane[0] + renderPoint[0], originPlane[1] + renderPoint[1]]
    const renderNormal: [number, number] = [-Math.sin(pathHeading), Math.cos(pathHeading)]
    const [longitude, latitude] = unprojectWebMercatorToBd09([
      pathPoint[0] + renderNormal[0] * appliedLateralOffsetScene,
      pathPoint[1] + renderNormal[1] * appliedLateralOffsetScene,
    ])
    const centerLocal: [number, number] = [
      pathPoint[0] - originPlane[0] + renderNormal[0] * appliedLateralOffsetScene,
      pathPoint[1] - originPlane[1] + renderNormal[1] * appliedLateralOffsetScene,
    ]
    const validateCornerAgainstTrack = (corner: [number, number], candidate: LaneTrack) => {
      const points = candidate.renderPoints
      return points.slice(1).some((end, index) => {
        const start = points[index]
        const dx = end[0] - start[0]
        const dy = end[1] - start[1]
        const length = Math.hypot(dx, dy)
        if (length <= 1e-6) return false
        const along = ((corner[0] - start[0]) * dx + (corner[1] - start[1]) * dy) / length
        const lateral = Math.abs((corner[0] - start[0]) * -dy + (corner[1] - start[1]) * dx) / length
        const longitudinalTolerance = vehicleHalfLengthMeters * horizontalScale + 0.15 * horizontalScale
        const withinLongitudinalRange = along >= (index === 0 ? -longitudinalTolerance : 0)
          && along <= (index === points.length - 2 ? length + longitudinalTolerance : length)
        return withinLongitudinalRange
          && lateral / horizontalScale <= candidate.widthMeters / 2 + 0.15
      })
    }
    const corners: [number, number][] = vehicleHalfLengthMeters > 0 && vehicleHalfWidthMeters > 0
      ? [-1, 1].flatMap((longitudinal) => [-1, 1].map((lateral) => ([
          centerLocal[0]
            + Math.cos(heading) * longitudinal * vehicleHalfLengthMeters * horizontalScale
            + renderNormal[0] * lateral * vehicleHalfWidthMeters * horizontalScale,
          centerLocal[1]
            + Math.sin(heading) * longitudinal * vehicleHalfLengthMeters * horizontalScale
            + renderNormal[1] * lateral * vehicleHalfWidthMeters * horizontalScale,
        ] as [number, number])))
      : [centerLocal]
    const pointInPolygon = (point: [number, number], polygon: [number, number][]) => {
      let inside = false
      for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
        const left = polygon[index]
        const right = polygon[previous]
        if (
          (left[1] > point[1]) !== (right[1] > point[1])
          && point[0] < (right[0] - left[0]) * (point[1] - left[1])
            / ((right[1] - left[1]) || 1e-12) + left[0]
        ) inside = !inside
      }
      return inside
    }
    const internalTrack = track.laneId.startsWith(':')
    const routeCorridorTracks = track.routeKey
      ? [...tracksByKey.values()].filter((candidate) => candidate.motionPathKey === track.motionPathKey)
      : []
    const corridorTracks = laneChanging && previousTrack
      ? [track, previousTrack]
      : routeCorridorTracks.length ? routeCorridorTracks : [track]
    const poseValid = internalTrack
      ? corners.every((corner) => (
          pointInPolygon(corner, manifest.junctionShape)
          || corridorTracks.some((candidate) => validateCornerAgainstTrack(corner, candidate))
        ))
      : corners.every((corner) => corridorTracks.some((candidate) => (
          validateCornerAgainstTrack(corner, candidate)
        )))
    if (!poseValid) return null
    const mappedFrontProgress = renderLength > 1e-6
      ? Math.max(0, Math.min(1, mappedFrontDistance / renderLength))
      : nearest.progress
    const mappedFrontPoint = samplePolyline(track.renderPoints, mappedFrontProgress)
    const mappedFrontHeading = tangentAtProgress(track.renderPoints, mappedFrontProgress) ?? pathHeading
    const mappedFrontNormal: [number, number] = [
      -Math.sin(mappedFrontHeading),
      Math.cos(mappedFrontHeading),
    ]
    const mappedFrontWithOffset: [number, number] = [
      mappedFrontPoint[0] + mappedFrontNormal[0] * appliedLateralOffsetScene,
      mappedFrontPoint[1] + mappedFrontNormal[1] * appliedLateralOffsetScene,
    ]
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
      arcDistanceMeters: sourceCenterDistanceMeters,
      pathArcDistanceMeters,
      minimumArcDistanceMeters,
      matchConfidence: Math.max(0, 1 - normalizedDistance * 0.65 - normalizedHeading * 0.35),
      transitionKind,
      modelCenterDistanceMeters: sourceCenterDistanceMeters,
      naturalFrontDistanceMeters: (
        sourceFrontDistance - track.sourceRenderStartDistanceSceneUnits
      ) / horizontalScale,
      stopFrontLimitDistanceMeters: stopFrontLimitDistance,
      stopClamped: false,
      sourceArcDistanceMeters: sourceFrontDistance / horizontalScale
        - Math.max(0, modelCenterOffsetMeters),
      sourceLateralOffsetMeters,
      sourceDistanceToLaneCenterMeters: Math.abs(sourceLateralOffsetScene) / horizontalScale,
      mappedArcDistanceMeters: sourceCenterDistanceMeters,
      roadMappingErrorMeters: Math.hypot(
        local[0] - mappedFrontWithOffset[0],
        local[1] - mappedFrontWithOffset[1],
      ) / horizontalScale,
      laneWidthMeters: track.widthMeters,
      corridorKind: internalTrack
        ? 'junction'
        : laneChanging ? 'lane_change_union' : 'lane',
      poseValid,
      mappingMode: options?.preserveSourceLateralOffset === true
        ? 'source_lateral'
        : 'centerline',
    }
  }) as LanePoseResolver
  resolver.motionPathSampler = motionPathSampler
  resolver.hasLane = (laneId: string) => tracks.has(laneId)
  resolver.covers = (laneId, coordinate) => {
    const projected = projectBd09ToWebMercator(coordinate)
    const local: [number, number] = [
      projected[0] - originPlane[0],
      projected[1] - originPlane[1],
    ]
    return (tracks.get(laneId) ?? []).some((track) => {
      const nearest = nearestPolylineProgress(local, track.sourcePoints)
      if (!nearest || nearest.distance > 4 * horizontalScale) return false
      const sourceDistance = nearest.progress * polylineLength(track.sourcePoints)
      return sourceDistance >= track.sourceRenderStartDistanceSceneUnits - 4 * horizontalScale
        && sourceDistance <= track.sourceRenderEndDistanceSceneUnits + 4 * horizontalScale
    })
  }
  resolver.coversDetailedArea = (coordinate) => {
    const projected = projectBd09ToWebMercator(coordinate)
    const radiusSceneUnits = manifest.radiusSceneUnits
      ?? (manifest.radiusMeters ?? 0) * horizontalScale
    return Math.hypot(projected[0] - originPlane[0], projected[1] - originPlane[1])
      <= radiusSceneUnits * 0.995
  }
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
