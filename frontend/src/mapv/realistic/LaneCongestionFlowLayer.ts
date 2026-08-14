import * as mapvthree from '@baidumap/mapv-three'
import * as THREE from 'three'

import type { CongestionLevel, TrafficStylePayload } from '../../types/intelligence'
import type { RealisticIntersectionManifest } from './intersectionManifest'
import type { RoadCoordinateProjector } from '../roadGeometry'
import {
  buildLaneCongestionFlows,
  CONGESTION_FLOW_VISUALS,
  type LaneCongestionFlow,
  type LaneCongestionFlowDiagnostics,
} from './laneCongestionFlow'

type VisibleCongestionLevel = Exclude<CongestionLevel, 'free'>
type SpeedBucket = 'low' | 'medium' | 'high'

const LEVELS: VisibleCongestionLevel[] = ['slow', 'congested', 'severe']
const SPEED_BUCKETS: SpeedBucket[] = ['low', 'medium', 'high']

function speedBucket(flow: LaneCongestionFlow): SpeedBucket {
  if (flow.animationSpeed < 0.42) return 'low'
  if (flow.animationSpeed < 0.64) return 'medium'
  return 'high'
}

function bucketSpeed(bucket: SpeedBucket): number {
  return bucket === 'low' ? 0.3 : bucket === 'medium' ? 0.52 : 0.76
}

function feature(flow: LaneCongestionFlow): Record<string, unknown> {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: flow.mapCoordinates },
    properties: {
      edge_id: flow.edgeId,
      lane_id: flow.laneId,
      congestion_level: flow.level,
      direction: flow.direction,
    },
  }
}

export class LaneCongestionFlowLayer {
  private readonly lines = new Map<string, mapvthree.Polyline>()
  private diagnostics: LaneCongestionFlowDiagnostics = {
    laneCount: 0,
    unmappedEdgeCount: 0,
    reverseFlowCount: 0,
  }
  private animationPaused = false
  private dataKey = ''

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
          animationInterval: 2,
          animationTailType: 1,
          animationTailRatio: 0.2,
          animationSpeed: bucketSpeed(bucket),
          animationIdle: 900,
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

  setTrafficStyle(
    manifests: readonly RealisticIntersectionManifest[],
    trafficStyle: TrafficStylePayload | null | undefined,
  ): void {
    const buckets = new Map<string, Record<string, unknown>[]>()
    let unmappedEdgeCount = 0
    let reverseFlowCount = 0
    let laneCount = 0
    for (const manifest of manifests) {
      const projectedOrigin = this.projector([
        manifest.origin.longitude,
        manifest.origin.latitude,
        0,
      ])
      const result = buildLaneCongestionFlows(
        manifest,
        trafficStyle,
        [projectedOrigin[0], projectedOrigin[1]],
      )
      unmappedEdgeCount += result.diagnostics.unmappedEdgeCount
      reverseFlowCount += result.diagnostics.reverseFlowCount
      laneCount += result.diagnostics.laneCount
      for (const flow of result.flows) {
        const key = `${flow.level}:${speedBucket(flow)}`
        buckets.set(key, [...(buckets.get(key) ?? []), feature(flow)])
      }
    }
    const dataKey = [...buckets.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, features]) => `${key}:${features.map((item) => {
        const properties = item.properties as Record<string, string>
        return `${properties.edge_id}/${properties.lane_id}`
      }).sort().join(',')}`)
      .join('|')
    this.diagnostics = { laneCount, unmappedEdgeCount, reverseFlowCount }
    if (dataKey === this.dataKey) return
    this.dataKey = dataKey
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
    this.animationPaused = false
    this.dataKey = ''
    this.diagnostics = { laneCount: 0, unmappedEdgeCount: 0, reverseFlowCount: 0 }
  }
}
