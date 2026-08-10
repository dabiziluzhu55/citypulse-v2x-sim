// 拓扑蓝线拥堵等级：有traffic_style时只消费后端结果，前端不再用另一套阈值重算

import type { CongestionLevel, TrafficStylePayload } from '../types/intelligence'
import type { SimulationSnapshot } from '../types/simulation'

export type { CongestionLevel }

export const CONGESTION_LEVEL_RANK: Record<CongestionLevel, number> = {
  free: 0,
  slow: 1,
  congested: 2,
  severe: 3,
}

export const CONGESTION_FLOW_COLORS: Record<CongestionLevel, string> = {
  free: '#00d9ff',
  slow: '#ffe566',
  congested: '#ffb020',
  severe: '#ff3b30',
}

export function normalizeCongestionLevel(value: unknown): CongestionLevel {
  if (value === 'slow' || value === 'congested' || value === 'severe' || value === 'free') {
    return value
  }
  return 'free'
}

export function worseCongestionLevel(
  left: CongestionLevel,
  right: CongestionLevel,
): CongestionLevel {
  return CONGESTION_LEVEL_RANK[left] >= CONGESTION_LEVEL_RANK[right] ? left : right
}

export function buildIntersectionCongestionLevels(
  snapshot: SimulationSnapshot | null | undefined,
  trafficStyle?: TrafficStylePayload | null,
): Record<string, CongestionLevel> {
  const levels: Record<string, CongestionLevel> = {}
  if (!snapshot) return levels

  const edges = trafficStyle?.edges
  if (!edges || Object.keys(edges).length === 0) {
    for (const intersectionId of Object.keys(snapshot.intersections ?? {})) {
      levels[intersectionId] = 'free'
    }
    return levels
  }

  for (const [intersectionId, intersection] of Object.entries(snapshot.intersections ?? {})) {
    let level: CongestionLevel = 'free'
    for (const [laneId, lane] of Object.entries(intersection.lanes ?? {})) {
      const edgeId = String(lane.edge_id || laneId.replace(/_\d+$/, ''))
      const styled = edges[edgeId]
      if (!styled) continue
      level = worseCongestionLevel(level, normalizeCongestionLevel(styled.level))
    }
    levels[intersectionId] = level
  }
  return levels
}

export function buildRouteCongestionLevels(
  routeIds: string[],
  intersectionLevels: Record<string, CongestionLevel>,
): Record<string, CongestionLevel> {
  const levels: Record<string, CongestionLevel> = {}
  for (const routeId of routeIds) {
    const [from, to] = routeId.split(':')
    const fromLevel = intersectionLevels[from] ?? 'free'
    const toLevel = intersectionLevels[to] ?? 'free'
    levels[routeId] = worseCongestionLevel(fromLevel, toLevel)
  }
  return levels
}
