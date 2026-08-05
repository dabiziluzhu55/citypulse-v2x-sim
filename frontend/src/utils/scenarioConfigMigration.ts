export const SCENARIO_CONFIG_EXPORT_VERSION = 6 as const

export const DEFAULT_MAJOR_EVENT_VEHICLE_COUNT = 20

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
