import {
  BAIDU_ROAD_SURFACE_Z,
  REALISTIC_INTERSECTION_SURFACE_Z,
  TOPOLOGY_BASE_CLEARANCE_Z,
  TOPOLOGY_ELEVATION_TRANSITION_METERS,
  TOPOLOGY_FLOW_CLEARANCE_Z,
} from './sceneElevation.ts'
import {
  topologyDistanceMeters,
  type IntersectionTopologyNode,
} from './intersectionTopology.ts'

export type TopologyFlowLayer = 'base' | 'flow'

function smoothstep(value: number): number {
  const clamped = Math.max(0, Math.min(1, value))
  return clamped * clamped * (3 - 2 * clamped)
}

export function localRoadElevationBlend(
  coordinate: readonly [number, number],
  nodes: IntersectionTopologyNode[],
): number {
  let blend = 0
  for (const node of nodes) {
    const radius = Math.max(TOPOLOGY_ELEVATION_TRANSITION_METERS, node.radiusMeters)
    const innerRadius = Math.max(0, radius - TOPOLOGY_ELEVATION_TRANSITION_METERS)
    const distance = topologyDistanceMeters(
      { longitude: coordinate[0], latitude: coordinate[1] },
      node,
    )
    if (distance >= radius) continue
    const localBlend = distance <= innerRadius
      ? 1
      : smoothstep((radius - distance) / Math.max(1e-6, radius - innerRadius))
    blend = Math.max(blend, localBlend)
  }
  return blend
}

export function topologyFlowHeight(
  coordinate: readonly [number, number],
  nodes: IntersectionTopologyNode[],
  layer: TopologyFlowLayer,
): number {
  const clearance = layer === 'base' ? TOPOLOGY_BASE_CLEARANCE_Z : TOPOLOGY_FLOW_CLEARANCE_Z
  const blend = localRoadElevationBlend(coordinate, nodes)
  const roadHeight = BAIDU_ROAD_SURFACE_Z
    + (REALISTIC_INTERSECTION_SURFACE_Z - BAIDU_ROAD_SURFACE_Z) * blend
  return roadHeight + clearance
}
