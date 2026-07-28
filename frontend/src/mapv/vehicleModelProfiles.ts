export interface VehicleModelProfile {
  modelType: number
  sourceLengthMeters: number
  targetLengthMeters: number
  modelForwardAxisAngle: number
  scale: [number, number, number]
}

function profile(
  modelType: number,
  sourceLengthMeters: number,
  targetLengthMeters: number,
): VehicleModelProfile {
  const scale = targetLengthMeters / sourceLengthMeters
  return {
    modelType,
    sourceLengthMeters,
    targetLengthMeters,
    modelForwardAxisAngle: 0,
    scale: [scale, scale, scale],
  }
}

export const CAR_MODEL_PROFILE = profile(3, 4.594, 5)
export const BUS_MODEL_PROFILE = profile(6, 9.66, 10)
export const TRUCK_MODEL_PROFILE = profile(10, 9.24, 9)

export function resolveVehicleModelProfile(typeId: string | undefined): VehicleModelProfile {
  const normalized = typeId?.toLowerCase() ?? ''
  if (normalized.includes('bus') || normalized.includes('coach')) return BUS_MODEL_PROFILE
  if (normalized.includes('truck') || normalized.includes('lorry')) return TRUCK_MODEL_PROFILE
  return CAR_MODEL_PROFILE
}
