export const REALISTIC_INTERSECTION_SURFACE_Z = 1.04
export const BAIDU_ROAD_SURFACE_Z = 0.25
export const TOPOLOGY_BASE_CLEARANCE_Z = 0.14
export const TOPOLOGY_FLOW_CLEARANCE_Z = 0.20
export const TOPOLOGY_ELEVATION_TRANSITION_METERS = 20

// Markings sit slightly above the asphalt, so the Twin anchor needs a small
// clearance to keep vehicle wheels visible without making the model float.
export const VEHICLE_MODEL_CLEARANCE_Z = 0.12

export const VEHICLE_MODEL_BASE_Z =
  REALISTIC_INTERSECTION_SURFACE_Z + VEHICLE_MODEL_CLEARANCE_Z
