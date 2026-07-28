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
}
export type LanePoseResolver = (
  laneId: string,
  coordinate: readonly [number, number],
) => ResolvedLanePose | null

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
  manifest: Pick<RealisticIntersectionManifest, 'edges' | 'origin' | 'horizontalScale'>,
  projector: RoadCoordinateProjector,
): LanePoseResolver {
  const lanes = new Map(
    manifest.edges.flatMap((edge) => edge.lanes.map((lane) => [lane.id, lane] as const)),
  )
  const projectedOrigin = projector([
    manifest.origin.longitude,
    manifest.origin.latitude,
    0,
  ])
  const originPlane = projectBd09ToWebMercator([projectedOrigin[0], projectedOrigin[1]])
  const maximumSnapDistance = 12 * (manifest.horizontalScale ?? 1)
  return (laneId, coordinate) => {
    const lane = lanes.get(laneId)
    if (!lane?.renderPoints?.length) return null
    const projected = projectBd09ToWebMercator(coordinate)
    const local: [number, number] = [
      projected[0] - originPlane[0],
      projected[1] - originPlane[1],
    ]
    const nearest = nearestPolylineProgress(local, lane.points)
    if (!nearest || nearest.distance > maximumSnapDistance) return null
    const renderPoint = samplePolyline(lane.renderPoints, nearest.progress)
    const heading = tangentAtProgress(lane.renderPoints, nearest.progress)
    if (heading == null) return null
    const [longitude, latitude] = unprojectWebMercatorToBd09([
      originPlane[0] + renderPoint[0],
      originPlane[1] + renderPoint[1],
    ])
    return { longitude, latitude, heading }
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
