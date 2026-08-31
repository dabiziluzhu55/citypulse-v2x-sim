// SUMO edge_id → 有向拓扑线段 集中映射与拥堵着色（2D/3D共用）

import type { CongestionLevel, TrafficStylePayload } from '../types/intelligence'
import {
  CONGESTION_FLOW_COLORS,
  normalizeCongestionLevel,
  worseCongestionLevel,
} from './topologyCongestion'

export interface EdgeTopologySegmentManifest {
  schemaVersion: 1
  edgeToSegment: Record<string, string>
  segments?: Record<string, string[]>
}

let edgeToSegment: Record<string, string> = {}
let loaded = false
const warnedMissingEdges = new Set<string>()
const pendingMissingEdges = new Set<string>()
let missingWarningScheduled = false

function flushMissingEdgeWarning(): void {
  missingWarningScheduled = false
  if (pendingMissingEdges.size === 0) return
  const missing = [...pendingMissingEdges]
  pendingMissingEdges.clear()
  const sample = missing.slice(0, 8).join('、')
  const remainder = missing.length > 8 ? ` 等 ${missing.length} 条` : ''
  console.warn(`[edge-topology] ${missing.length} 条边缺少拓扑映射，保留默认道路颜色：${sample}${remainder}`)
}

export function isInternalSumoEdgeId(edgeId: string): boolean {
  return edgeId.startsWith(':')
}

export function parseEdgeTopologySegmentManifest(value: unknown): EdgeTopologySegmentManifest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('edge topology segment map must be an object')
  }
  const source = value as Partial<EdgeTopologySegmentManifest>
  if (source.schemaVersion !== 1 || !source.edgeToSegment || typeof source.edgeToSegment !== 'object') {
    throw new Error('edge topology segment map is incompatible')
  }
  const map: Record<string, string> = {}
  for (const [edgeId, segmentId] of Object.entries(source.edgeToSegment)) {
    if (isInternalSumoEdgeId(edgeId)) continue
    if (typeof segmentId !== 'string' || !segmentId.includes(':')) {
      throw new Error(`invalid topology segment for edge ${edgeId}`)
    }
    map[edgeId] = segmentId
  }
  return {
    schemaVersion: 1,
    edgeToSegment: map,
    segments: source.segments && typeof source.segments === 'object' ? source.segments : undefined,
  }
}

export async function loadEdgeTopologySegmentMap(
  url = '/intersections/v3/edge-to-topology-segment.json',
): Promise<Record<string, string>> {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`edge topology segment map returned HTTP ${response.status}`)
  }
  const manifest = parseEdgeTopologySegmentManifest(await response.json())
  edgeToSegment = manifest.edgeToSegment
  loaded = true
  warnedMissingEdges.clear()
  pendingMissingEdges.clear()
  return edgeToSegment
}

export function setEdgeTopologySegmentMapForTests(map: Record<string, string>): void {
  edgeToSegment = { ...map }
  loaded = true
  warnedMissingEdges.clear()
  pendingMissingEdges.clear()
}

export function resolveTopologySegmentId(edgeId: string): string | null {
  if (!edgeId || isInternalSumoEdgeId(edgeId)) return null
  const segmentId = edgeToSegment[edgeId]
  if (segmentId) return segmentId
  if (loaded && import.meta.env.DEV && !warnedMissingEdges.has(edgeId)) {
    warnedMissingEdges.add(edgeId)
    pendingMissingEdges.add(edgeId)
    if (!missingWarningScheduled) {
      missingWarningScheduled = true
      queueMicrotask(flushMissingEdgeWarning)
    }
  }
  return null
}

/**
 * 按 edge 拥堵状态着色有向拓扑线段；不再用路口端点最差等级推断整条路。
 */
export function buildDirectedRouteCongestionLevels(
  trafficStyle: TrafficStylePayload | null | undefined,
  routeIds: string[],
): Record<string, CongestionLevel> {
  const levels: Record<string, CongestionLevel> = {}
  for (const routeId of routeIds) levels[routeId] = 'free'
  const edges = trafficStyle?.edges
  if (!edges) return levels
  for (const [edgeId, style] of Object.entries(edges)) {
    if (isInternalSumoEdgeId(edgeId)) continue
    const segmentId = resolveTopologySegmentId(edgeId)
    if (!segmentId) continue
    if (!(segmentId in levels)) continue
    levels[segmentId] = worseCongestionLevel(
      levels[segmentId],
      normalizeCongestionLevel(style.level),
    )
  }
  return levels
}

export { CONGESTION_FLOW_COLORS }
