import type { RoadCoordinateProjector } from './roadGeometry.ts'
import { normalizeRadians } from './vehicleOrientation.ts'

const METERS_PER_DEGREE_LATITUDE = 110_900
const MAX_INTERPOLATED_ANCHORS = 3
const MIN_AXIS_LENGTH = 0.1
const MAX_AXIS_LENGTH = 10
const MIN_AXIS_SINE = 0.5

export interface SumoHeadingTransform {
  xAxis: [number, number]
  yAxis: [number, number]
  determinant: number
  sourceSha256: string
}

export interface SumoHeadingAnchor {
  intersectionId: string
  longitude: number
  latitude: number
  radiusMeters: number
  sumoHeadingTransform: SumoHeadingTransform
}

export interface ResolvedSumoHeading {
  heading: number
  anchorIds: string[]
  local: boolean
}

interface ProjectedHeadingAnchor extends SumoHeadingAnchor {
  point: [number, number]
}

export function sumoHeadingTransformIsValid(
  value: unknown,
): value is SumoHeadingTransform {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const transform = value as Partial<SumoHeadingTransform>
  if (
    !Array.isArray(transform.xAxis)
    || transform.xAxis.length !== 2
    || !transform.xAxis.every(Number.isFinite)
    || !Array.isArray(transform.yAxis)
    || transform.yAxis.length !== 2
    || !transform.yAxis.every(Number.isFinite)
    || !Number.isFinite(Number(transform.determinant))
    || typeof transform.sourceSha256 !== 'string'
    || !/^[a-f0-9]{64}$/.test(transform.sourceSha256)
  ) return false
  const xLength = Math.hypot(transform.xAxis[0], transform.xAxis[1])
  const yLength = Math.hypot(transform.yAxis[0], transform.yAxis[1])
  if (
    xLength < MIN_AXIS_LENGTH
    || xLength > MAX_AXIS_LENGTH
    || yLength < MIN_AXIS_LENGTH
    || yLength > MAX_AXIS_LENGTH
  ) return false
  const declaredDeterminant = Number(transform.determinant)
  const determinant = transform.xAxis[0] * transform.yAxis[1]
    - transform.xAxis[1] * transform.yAxis[0]
  const axisSine = determinant / (xLength * yLength)
  return determinant > 0
    && Math.abs(determinant - declaredDeterminant) <= 1e-6
    && axisSine >= MIN_AXIS_SINE
}

export function sumoNavigationAngleToMapHeading(
  angleDegrees: number,
  transform: SumoHeadingTransform,
): number | null {
  if (!Number.isFinite(angleDegrees) || !sumoHeadingTransformIsValid(transform)) return null
  const angleRadians = angleDegrees * Math.PI / 180
  const sourceX = Math.sin(angleRadians)
  const sourceY = Math.cos(angleRadians)
  const mapX = sourceX * transform.xAxis[0] + sourceY * transform.yAxis[0]
  const mapY = sourceX * transform.xAxis[1] + sourceY * transform.yAxis[1]
  if (Math.hypot(mapX, mapY) <= 1e-9) return null
  return normalizeRadians(Math.atan2(mapY, mapX))
}

function geographicDistanceMeters(
  a: readonly [number, number],
  b: readonly [number, number],
): number {
  const latitude = (a[1] + b[1]) / 2 * Math.PI / 180
  const east = (a[0] - b[0]) * Math.cos(latitude) * METERS_PER_DEGREE_LATITUDE
  const north = (a[1] - b[1]) * METERS_PER_DEGREE_LATITUDE
  return Math.hypot(east, north)
}

function blendTransforms(
  candidates: Array<{ anchor: ProjectedHeadingAnchor; distanceMeters: number }>,
): SumoHeadingTransform | null {
  if (candidates.length === 0) return null
  const exact = candidates.find((candidate) => candidate.distanceMeters <= 0.01)
  if (exact) return exact.anchor.sumoHeadingTransform
  const sourceSha256 = candidates[0].anchor.sumoHeadingTransform.sourceSha256
  if (candidates.some((candidate) => (
    candidate.anchor.sumoHeadingTransform.sourceSha256 !== sourceSha256
  ))) return null
  let weightTotal = 0
  const xAxis: [number, number] = [0, 0]
  const yAxis: [number, number] = [0, 0]
  for (const { anchor, distanceMeters } of candidates) {
    const weight = 1 / Math.max(1, distanceMeters) ** 2
    weightTotal += weight
    xAxis[0] += anchor.sumoHeadingTransform.xAxis[0] * weight
    xAxis[1] += anchor.sumoHeadingTransform.xAxis[1] * weight
    yAxis[0] += anchor.sumoHeadingTransform.yAxis[0] * weight
    yAxis[1] += anchor.sumoHeadingTransform.yAxis[1] * weight
  }
  if (weightTotal <= 0) return null
  xAxis[0] /= weightTotal
  xAxis[1] /= weightTotal
  yAxis[0] /= weightTotal
  yAxis[1] /= weightTotal
  const transform: SumoHeadingTransform = {
    xAxis,
    yAxis,
    determinant: xAxis[0] * yAxis[1] - xAxis[1] * yAxis[0],
    sourceSha256,
  }
  return sumoHeadingTransformIsValid(transform) ? transform : null
}

export class SumoHeadingField {
  private anchors: ProjectedHeadingAnchor[] = []
  private preferredIntersectionId: string | null = null
  private readonly projector: RoadCoordinateProjector

  constructor(projector: RoadCoordinateProjector) {
    this.projector = projector
  }

  setAnchors(anchors: readonly SumoHeadingAnchor[]): void {
    const sourceHashes = new Set(anchors.map((anchor) => anchor.sumoHeadingTransform.sourceSha256))
    if (anchors.length > 0 && sourceHashes.size !== 1) {
      throw new Error('SUMO heading anchors do not share one source hash')
    }
    this.anchors = anchors.map((anchor) => {
      if (!sumoHeadingTransformIsValid(anchor.sumoHeadingTransform)) {
        throw new Error(`Intersection ${anchor.intersectionId} has an invalid SUMO heading transform`)
      }
      const projected = this.projector([anchor.longitude, anchor.latitude, 0])
      return {
        ...anchor,
        point: [projected[0], projected[1]],
      }
    })
  }

  setPreferredIntersection(intersectionId: string | null): void {
    this.preferredIntersectionId = intersectionId
  }

  resolve(
    angleDegrees: number,
    point: readonly [number, number],
  ): ResolvedSumoHeading | null {
    if (this.anchors.length === 0) return null
    const candidates = this.anchors
      .map((anchor) => ({
        anchor,
        distanceMeters: geographicDistanceMeters(point, anchor.point),
      }))
      .sort((left, right) => left.distanceMeters - right.distanceMeters)
    const preferred = this.preferredIntersectionId
      ? candidates.find((candidate) => (
          candidate.anchor.intersectionId === this.preferredIntersectionId
        ))
      : null
    if (preferred && preferred.distanceMeters <= preferred.anchor.radiusMeters) {
      const heading = sumoNavigationAngleToMapHeading(
        angleDegrees,
        preferred.anchor.sumoHeadingTransform,
      )
      return heading == null ? null : {
        heading,
        anchorIds: [preferred.anchor.intersectionId],
        local: true,
      }
    }
    const selected = candidates.slice(0, MAX_INTERPOLATED_ANCHORS)
    const transform = blendTransforms(selected)
    const heading = transform
      ? sumoNavigationAngleToMapHeading(angleDegrees, transform)
      : null
    return heading == null ? null : {
      heading,
      anchorIds: selected.map((candidate) => candidate.anchor.intersectionId),
      local: false,
    }
  }
}
