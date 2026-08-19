import type { CongestionLevel } from '../../types/intelligence'
import type { LaneCongestionStateSnapshot } from '../../utils/laneCongestionState'
import {
  projectBd09ToWebMercator,
  unprojectWebMercatorToBd09,
} from '../sceneCoordinates.ts'
import type {
  Point2,
  RealisticIntersectionManifest,
  RealisticLane,
} from './intersectionManifest'
import { REALISTIC_INTERSECTION_SURFACE_Z } from '../sceneElevation.ts'

export interface LaneCongestionFlow {
  edgeId: string
  laneId: string
  level: Exclude<CongestionLevel, 'free'>
  color: string
  direction: 'forward'
  animationSpeed: number
  mapCoordinates: Array<[number, number, number]>
}

export interface LaneCongestionFlowDiagnostics {
  laneCount: number
  unmappedEdgeCount: number
  reverseFlowCount: number
  dataSourceRebuildCount: number
  speedBucketStabilizationCount: number
}

export type LaneFlowSpeedBucket = 'low' | 'medium' | 'high'

interface StableLaneFlowSpeedBucketState {
  current: LaneFlowSpeedBucket
  pending: LaneFlowSpeedBucket | null
  pendingCount: number
}

export class LaneFlowSpeedBucketStabilizer {
  private readonly states = new Map<string, StableLaneFlowSpeedBucketState>()

  resolve(
    key: string,
    proposed: LaneFlowSpeedBucket,
  ): { bucket: LaneFlowSpeedBucket; suppressed: boolean } {
    const state = this.states.get(key)
    if (!state) {
      this.states.set(key, { current: proposed, pending: null, pendingCount: 0 })
      return { bucket: proposed, suppressed: false }
    }
    if (state.current === proposed) {
      state.pending = null
      state.pendingCount = 0
      return { bucket: state.current, suppressed: false }
    }
    if (state.pending === proposed) state.pendingCount += 1
    else {
      state.pending = proposed
      state.pendingCount = 1
    }
    if (state.pendingCount >= 3) {
      state.current = proposed
      state.pending = null
      state.pendingCount = 0
      return { bucket: state.current, suppressed: false }
    }
    return { bucket: state.current, suppressed: true }
  }

  retain(keys: ReadonlySet<string>): void {
    for (const key of this.states.keys()) {
      if (!keys.has(key)) this.states.delete(key)
    }
  }

  clear(): void {
    this.states.clear()
  }
}

export const CONGESTION_FLOW_VISUALS: Record<CongestionLevel, {
  visible: boolean
  color: string
  lineWidth: number
  opacity: number
}> = {
  free: { visible: false, color: '#087dff', lineWidth: 0, opacity: 0 },
  slow: { visible: true, color: '#ffe978', lineWidth: 1.6, opacity: 0.7 },
  congested: { visible: true, color: '#ffd21f', lineWidth: 2.5, opacity: 0.94 },
  severe: { visible: true, color: '#ff3141', lineWidth: 3.1, opacity: 0.98 },
}

function laneGuidePoints(lane: RealisticLane): Point2[] {
  return lane.vehicleGuidePoints?.length
    ? lane.vehicleGuidePoints
    : lane.renderPoints?.length
      ? lane.renderPoints
      : lane.points
}

export function congestionAnimationSpeed(meanSpeedMetersPerSecond: number): number {
  if (!Number.isFinite(meanSpeedMetersPerSecond)) return 0.42
  return Math.max(0.22, Math.min(0.82, 0.22 + meanSpeedMetersPerSecond / 22))
}

export function buildLaneCongestionFlows(
  manifest: RealisticIntersectionManifest,
  congestionState: LaneCongestionStateSnapshot | null | undefined,
  mapOrigin = manifest.origin.bd09,
): { flows: LaneCongestionFlow[]; diagnostics: LaneCongestionFlowDiagnostics } {
  const flows: LaneCongestionFlow[] = []
  const laneStates = congestionState?.lanes ?? {}
  const originBd09 = mapOrigin
  if (!originBd09) {
    return {
      flows,
      diagnostics: {
        laneCount: 0,
        unmappedEdgeCount: 0,
        reverseFlowCount: 0,
        dataSourceRebuildCount: 0,
        speedBucketStabilizationCount: 0,
      },
    }
  }
  const originMercator = projectBd09ToWebMercator(originBd09)
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      if ((lane.kind ?? 'driving') !== 'driving') continue
      const state = laneStates[lane.id]
      if (!state || state.level === 'free') continue
      const level = state.level
      const visual = CONGESTION_FLOW_VISUALS[level]
      const points = laneGuidePoints(lane)
      if (points.length < 2) continue
      flows.push({
        edgeId: edge.id,
        laneId: lane.id,
        level,
        color: visual.color,
        direction: 'forward',
        animationSpeed: congestionAnimationSpeed(state.meanSpeed),
        mapCoordinates: points.map((point) => {
          const coordinate = unprojectWebMercatorToBd09([
            originMercator[0] + point[0],
            originMercator[1] + point[1],
          ])
          return [coordinate[0], coordinate[1], REALISTIC_INTERSECTION_SURFACE_Z + 0.16]
        }),
      })
    }
  }
  return {
    flows,
    diagnostics: {
      laneCount: flows.length,
      unmappedEdgeCount: 0,
      reverseFlowCount: 0,
      dataSourceRebuildCount: 0,
      speedBucketStabilizationCount: 0,
    },
  }
}
