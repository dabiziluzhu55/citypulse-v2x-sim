import * as mapvthree from '@baidumap/mapv-three'
import * as THREE from 'three'

import type { CongestionLevel } from '../../types/intelligence'
import type { EventLanePositionIndexEntry } from '../../utils/eventLanePositionIndex'
import type { LaneCongestionStateSnapshot } from '../../utils/laneCongestionState'
import type { RealisticIntersectionManifest } from './intersectionManifest'
import type { RoadCoordinateProjector } from '../roadGeometry'
import { REALISTIC_INTERSECTION_SURFACE_Z } from '../sceneElevation'
import {
  buildLaneCongestionFlows,
  CONGESTION_FLOW_VISUALS,
  LaneFlowSpeedBucketStabilizer,
  type LaneCongestionFlow,
  type LaneCongestionFlowDiagnostics,
  type LaneFlowSpeedBucket,
} from './laneCongestionFlow'

type VisibleCongestionLevel = Exclude<CongestionLevel, 'free'>
type SpeedBucket = LaneFlowSpeedBucket

const LEVELS: VisibleCongestionLevel[] = ['slow', 'congested', 'severe']
const SPEED_BUCKETS: SpeedBucket[] = ['low', 'medium', 'high']
function speedBucket(flow: LaneCongestionFlow): SpeedBucket {
  if (flow.animationSpeed < 0.42) return 'low'
  if (flow.animationSpeed < 0.64) return 'medium'
  return 'high'
}

function bucketSpeed(bucket: SpeedBucket): number {
  return bucket === 'low' ? 0.18 : bucket === 'medium' ? 0.30 : 0.44
}

function feature(flow: LaneCongestionFlow): Record<string, unknown> {
  const first = flow.mapCoordinates[0]
  const last = flow.mapCoordinates.at(-1)
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: flow.mapCoordinates },
    properties: {
      edge_id: flow.edgeId,
      lane_id: flow.laneId,
      congestion_level: flow.level,
      direction: flow.direction,
      geometry_signature: first && last
        ? `${flow.mapCoordinates.length}:${first[0].toFixed(7)},${first[1].toFixed(7)}:${last[0].toFixed(7)},${last[1].toFixed(7)}`
        : 'empty',
    },
  }
}

export class LaneCongestionFlowLayer {
  private readonly lines = new Map<string, mapvthree.Polyline>()
  private diagnostics: LaneCongestionFlowDiagnostics = {
    laneCount: 0,
    unmappedEdgeCount: 0,
    reverseFlowCount: 0,
    dataSourceRebuildCount: 0,
    speedBucketStabilizationCount: 0,
  }
  private animationPaused = false
  private dataKey = ''
  private readonly speedBuckets = new LaneFlowSpeedBucketStabilizer()
  private dataSourceRebuildCount = 0
  private speedBucketStabilizationCount = 0

  constructor(
    private readonly engine: mapvthree.Engine,
    private readonly projector: RoadCoordinateProjector,
  ) {
    for (const level of LEVELS) {
      const visual = CONGESTION_FLOW_VISUALS[level]
      for (const bucket of SPEED_BUCKETS) {
        const line = engine.add(new mapvthree.Polyline({
          flat: true,
          isCurve: false,
          color: new THREE.Color(visual.color),
          lineWidth: visual.lineWidth,
          keepSize: true,
          transparent: true,
          opacity: visual.opacity,
          enableAnimation: true,
          enableAnimationChaos: false,
          animationInterval: 4,
          animationTailType: 1,
          animationTailRatio: 0.28,
          animationSpeed: bucketSpeed(bucket),
          animationIdle: 1_500,
          height: 0,
        }))
        line.position.z = 0
        line.renderOrder = 48
        line.material.transparent = true
        line.material.depthTest = true
        line.material.depthWrite = false
        line.material.blending = THREE.AdditiveBlending
        this.lines.set(`${level}:${bucket}`, line)
      }
    }
  }

  get animationActive(): boolean {
    return this.diagnostics.laneCount > 0
  }

  setLaneCongestion(
    manifests: readonly RealisticIntersectionManifest[],
    congestionState: LaneCongestionStateSnapshot | null | undefined,
    indexedLanes: readonly EventLanePositionIndexEntry[] = [],
  ): void {
    const buckets = new Map<string, Record<string, unknown>[]>()
    let unmappedEdgeCount = 0
    let reverseFlowCount = 0
    let laneCount = 0
    const activeLaneKeys = new Set<string>()
    const localLaneIds = new Set<string>()
    for (const manifest of manifests) {
      for (const edge of manifest.edges) {
        for (const lane of edge.lanes) localLaneIds.add(lane.id)
      }
      const projectedOrigin = this.projector([
        manifest.origin.longitude,
        manifest.origin.latitude,
        0,
      ])
      const result = buildLaneCongestionFlows(
        manifest,
        congestionState,
        [projectedOrigin[0], projectedOrigin[1]],
      )
      unmappedEdgeCount += result.diagnostics.unmappedEdgeCount
      reverseFlowCount += result.diagnostics.reverseFlowCount
      laneCount += result.diagnostics.laneCount
      for (const flow of result.flows) {
        const laneKey = `${flow.edgeId}:${flow.laneId}`
        activeLaneKeys.add(laneKey)
        const stabilized = this.speedBuckets.resolve(laneKey, speedBucket(flow))
        if (stabilized.suppressed) this.speedBucketStabilizationCount += 1
        const key = `${flow.level}:${stabilized.bucket}`
        buckets.set(key, [...(buckets.get(key) ?? []), feature(flow)])
      }
    }
    const indexedByLaneId = new Map(indexedLanes.map((entry) => [entry.laneId, entry] as const))
    for (const state of Object.values(congestionState?.lanes ?? {})) {
      if (state.level === 'free' || localLaneIds.has(state.laneId)) continue
      const entry = indexedByLaneId.get(state.laneId)
      if (!entry || entry.kind !== 'driving' || entry.coordinates.length < 2) {
        unmappedEdgeCount += 1
        continue
      }
      const visual = CONGESTION_FLOW_VISUALS[state.level]
      const flow: LaneCongestionFlow = {
        edgeId: state.edgeId,
        laneId: state.laneId,
        level: state.level,
        color: visual.color,
        direction: 'forward',
        animationSpeed: Math.max(0.22, Math.min(0.82, 0.22 + state.meanSpeed / 22)),
        mapCoordinates: entry.coordinates.map(([longitude, latitude]) => {
          const point = this.projector([longitude, latitude, REALISTIC_INTERSECTION_SURFACE_Z + 0.16])
          return [point[0], point[1], point[2] ?? REALISTIC_INTERSECTION_SURFACE_Z + 0.16]
        }),
      }
      laneCount += 1
      const laneKey = `${flow.edgeId}:${flow.laneId}`
      activeLaneKeys.add(laneKey)
      const stabilized = this.speedBuckets.resolve(laneKey, speedBucket(flow))
      if (stabilized.suppressed) this.speedBucketStabilizationCount += 1
      const key = `${flow.level}:${stabilized.bucket}`
      buckets.set(key, [...(buckets.get(key) ?? []), feature(flow)])
    }
    this.speedBuckets.retain(activeLaneKeys)
    const dataKey = [...buckets.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, features]) => `${key}:${features.map((item) => {
        const properties = item.properties as Record<string, string>
        return `${properties.edge_id}/${properties.lane_id}/${properties.geometry_signature}`
      }).sort().join(',')}`)
      .join('|')
    this.diagnostics = {
      laneCount,
      unmappedEdgeCount,
      reverseFlowCount,
      dataSourceRebuildCount: this.dataSourceRebuildCount,
      speedBucketStabilizationCount: this.speedBucketStabilizationCount,
    }
    if (dataKey === this.dataKey) return
    this.dataKey = dataKey
    this.dataSourceRebuildCount += 1
    this.diagnostics.dataSourceRebuildCount = this.dataSourceRebuildCount
    for (const [key, line] of this.lines) {
      const features = buckets.get(key) ?? []
      line.dataSource?.clear()
      line.dataSource = features.length
        ? mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features })
        : null
    }
    this.engine.requestRender()
  }

  setAnimationPaused(paused: boolean): void {
    if (paused === this.animationPaused) return
    this.animationPaused = paused
    for (const line of this.lines.values()) line.enableAnimation = !paused
  }

  stats(): LaneCongestionFlowDiagnostics {
    return { ...this.diagnostics }
  }

  destroy(): void {
    for (const line of this.lines.values()) {
      line.dataSource?.clear()
      this.engine.remove(line)
    }
    this.lines.clear()
    this.speedBuckets.clear()
    this.animationPaused = false
    this.dataKey = ''
    this.dataSourceRebuildCount = 0
    this.speedBucketStabilizationCount = 0
    this.diagnostics = {
      laneCount: 0,
      unmappedEdgeCount: 0,
      reverseFlowCount: 0,
      dataSourceRebuildCount: 0,
      speedBucketStabilizationCount: 0,
    }
  }
}
