export const REALISTIC_INTERSECTION_SURFACE_Z = 1.04

// Markings sit slightly above the asphalt, so the Twin anchor needs a small
// clearance to keep vehicle wheels visible without making the model float.
export const VEHICLE_MODEL_CLEARANCE_Z = 0.12

export const VEHICLE_MODEL_BASE_Z =
  REALISTIC_INTERSECTION_SURFACE_Z + VEHICLE_MODEL_CLEARANCE_Z
