export interface VehicleModelProfile {
  modelType: number
  sourceLengthMeters: number
  sourceWidthMeters: number
  sourceHeightMeters: number
  targetLengthMeters: number
  targetWidthMeters: number
  modelForwardAxisAngle: number
  maximumAccelerationMetersPerSecondSquared: number
  scale: [number, number, number]
}

export const ELECTRIC_BICYCLE_MODEL_TYPE = 28

function profile(
  modelType: number,
  sourceLengthMeters: number,
  sourceWidthMeters: number,
  sourceHeightMeters: number,
  targetLengthMeters: number,
  targetWidthMeters: number,
  modelForwardAxisAngle: number,
  maximumAccelerationMetersPerSecondSquared: number,
): VehicleModelProfile {
  const longitudinalScale = targetLengthMeters / sourceLengthMeters
  const lateralScale = targetWidthMeters / sourceWidthMeters
  return {
    modelType,
    sourceLengthMeters,
    sourceWidthMeters,
    sourceHeightMeters,
    targetLengthMeters,
    targetWidthMeters,
    modelForwardAxisAngle,
    maximumAccelerationMetersPerSecondSquared,
    // MapV rotates the model +90 degrees around X, so local Y becomes map Z
    // (height) and local Z becomes map Y (width).
    scale: [longitudinalScale, longitudinalScale, lateralScale],
  }
}

export const CAR_MODEL_PROFILE = profile(3, 4.594, 1.988, 1.413, 5, 1.8, 0, 2.6)
export const BUS_MODEL_PROFILE = profile(6, 9.66, 2.973, 2.977, 12, 2.5, 0, 1.2)
export const TRUCK_MODEL_PROFILE = profile(10, 9.243, 2.739, 3.29, 10, 2.5, 0, 1.3)
export const ELECTRIC_BICYCLE_MODEL_PROFILE = profile(
  ELECTRIC_BICYCLE_MODEL_TYPE,
  2.018,
  0.625,
  1.723,
  1.8,
  0.65,
  0,
  1.5,
)

export function resolveVehicleModelProfile(typeId: string | undefined): VehicleModelProfile {
  const normalized = typeId?.toLowerCase() ?? ''
  if (normalized.includes('electric_bicycle') || normalized.includes('ebike')) {
    return ELECTRIC_BICYCLE_MODEL_PROFILE
  }
  if (normalized.includes('bus') || normalized.includes('coach')) return BUS_MODEL_PROFILE
  if (normalized.includes('truck') || normalized.includes('lorry')) return TRUCK_MODEL_PROFILE
  return CAR_MODEL_PROFILE
}
