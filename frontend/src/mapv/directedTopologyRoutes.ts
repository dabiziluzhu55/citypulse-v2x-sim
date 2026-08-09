// 将无向拓扑几何展开为有向线段（正反向坐标），供2D/3D共用

import type { IntersectionTopologyRoute } from './intersectionTopologyRoutes'

/** 对每条无向 route 生成 from→to 与 to→from 两条有向线段 */
export function expandDirectedTopologyRoutes(
  routes: IntersectionTopologyRoute[],
): IntersectionTopologyRoute[] {
  const directed: IntersectionTopologyRoute[] = []
  const seen = new Set<string>()
  for (const route of routes) {
    const forwardId = `${route.from}:${route.to}`
    const reverseId = `${route.to}:${route.from}`
    if (!seen.has(forwardId)) {
      seen.add(forwardId)
      directed.push({
        routeId: forwardId,
        from: route.from,
        to: route.to,
        lengthMeters: route.lengthMeters,
        coordinates: route.coordinates.map((point) => [point[0], point[1]] as [number, number]),
      })
    }
    if (!seen.has(reverseId)) {
      seen.add(reverseId)
      directed.push({
        routeId: reverseId,
        from: route.to,
        to: route.from,
        lengthMeters: route.lengthMeters,
        coordinates: [...route.coordinates]
          .reverse()
          .map((point) => [point[0], point[1]] as [number, number]),
      })
    }
  }
  return directed.sort((left, right) => left.routeId.localeCompare(right.routeId, undefined, { numeric: true }))
}
