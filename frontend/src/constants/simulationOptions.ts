export const ACTIVE_SESSION_ID_KEY = 'citypulse.active_session_id'
export const ACTIVE_SIMULATION_CONTEXT_KEY = 'citypulse.active_simulation_context'

export const STATUS_POLL_INTERVAL_MS = 2_000
export const SIMULATION_SNAPSHOT_INTERVAL_MS = 200

export const DEFAULT_INTERSECTION_ID = 'demo_2'

export const CONTROL_MODE_LABELS: Record<string, string> = {
  fixed: '固定配时算法',
  max_pressure: 'Max Pressure算法',
  sotl: 'SOTL自组织信号算法',
}

export const DASHBOARD_CONTROL_MODES = [
  { value: 'fixed', label: CONTROL_MODE_LABELS.fixed, backendSupported: true },
  { value: 'max_pressure', label: CONTROL_MODE_LABELS.max_pressure, backendSupported: true },
  { value: 'sotl', label: CONTROL_MODE_LABELS.sotl, backendSupported: true },
] as const

export function resolveDashboardControlModes(controlModes: string[]) {
  return controlModes.map((value) => ({
    value,
    label: resolveControlModeLabel(value),
    backendSupported: true,
  }))
}

export function isBackendControlMode(mode: string): boolean {
  return DASHBOARD_CONTROL_MODES.some((item) => item.value === mode && item.backendSupported)
}

export function resolveControlModeLabel(mode: string): string {
  return CONTROL_MODE_LABELS[mode] ?? mode
}
