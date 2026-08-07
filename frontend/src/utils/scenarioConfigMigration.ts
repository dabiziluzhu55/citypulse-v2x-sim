export const SCENARIO_CONFIG_EXPORT_VERSION = 6 as const

export const DEFAULT_MAJOR_EVENT_VEHICLE_COUNT = 20
export const MIN_MAJOR_EVENT_VEHICLE_COUNT = 20
export const MAX_MAJOR_EVENT_VEHICLE_COUNT = 200

export function resolveMajorEventVehicleCount(value: unknown): number {
  if (value == null) return DEFAULT_MAJOR_EVENT_VEHICLE_COUNT
  if (
    typeof value !== 'number'
    || !Number.isInteger(value)
    || value < MIN_MAJOR_EVENT_VEHICLE_COUNT
    || value > MAX_MAJOR_EVENT_VEHICLE_COUNT
  ) {
    throw new Error(
      `大型活动车辆数必须为 ${MIN_MAJOR_EVENT_VEHICLE_COUNT}-${MAX_MAJOR_EVENT_VEHICLE_COUNT} 的整数`,
    )
  }
  return value
}

export function resolveImportedDisturbanceTimes(
  value: Record<string, unknown>,
  simulationStartTime: string,
  simulationEndTime: string,
): { startTime: string; endTime: string } {
  return {
    startTime: typeof value.start_time === 'string' ? value.start_time : simulationStartTime,
    endTime: typeof value.end_time === 'string' ? value.end_time : simulationEndTime,
  }
}
