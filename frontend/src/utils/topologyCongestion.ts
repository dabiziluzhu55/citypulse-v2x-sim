// 根据traffic_style与停车数计算拓扑蓝线拥堵等级

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

function levelFromLaneMetrics(input: {
  vehicle_count: number
  halting_count: number
  mean_speed: number
  occupancy: number
}): CongestionLevel {
  if (input.vehicle_count <= 0) return 'free'
  const haltRatio = input.halting_count / Math.max(input.vehicle_count, 1)
  if (input.mean_speed <= 1 && (input.occupancy >= 35 || haltRatio >= 0.6)) return 'severe'
  if (input.mean_speed <= 3 && (input.occupancy >= 20 || haltRatio >= 0.4)) return 'congested'
  if (input.mean_speed <= 8 && (input.occupancy >= 10 || haltRatio >= 0.2)) return 'slow'
  return 'free'
}

export function buildIntersectionCongestionLevels(
  snapshot: SimulationSnapshot | null | undefined,
  trafficStyle?: TrafficStylePayload | null,
): Record<string, CongestionLevel> {
  const levels: Record<string, CongestionLevel> = {}
  if (!snapshot) return levels
  for (const [intersectionId, intersection] of Object.entries(snapshot.intersections ?? {})) {
    let level: CongestionLevel = 'free'
    for (const [laneId, lane] of Object.entries(intersection.lanes ?? {})) {
      const edgeId = String(lane.edge_id || laneId.replace(/_\d+$/, ''))
      const styled = trafficStyle?.edges?.[edgeId]
      const laneLevel = styled
        ? normalizeCongestionLevel(styled.level)
        : levelFromLaneMetrics(lane)
      level = worseCongestionLevel(level, laneLevel)
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
