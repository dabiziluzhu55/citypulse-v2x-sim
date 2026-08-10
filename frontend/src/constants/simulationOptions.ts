export const ACTIVE_SESSION_ID_KEY = 'citypulse.active_session_id'
export const ACTIVE_SIMULATION_CONTEXT_KEY = 'citypulse.active_simulation_context'

export const STATUS_POLL_INTERVAL_MS = 2_000
// Two full-network snapshots per second are sufficient for buffered animation and
// substantially reduce JSON serialization pressure in 20-intersection sessions.
export const SIMULATION_SNAPSHOT_INTERVAL_MS = 500

export const DEFAULT_INTERSECTION_ID = 'demo_2'

export const SUPPORTED_BACKEND_CONTROL_MODES = [
  'fixed',
  'max_pressure',
  'sotl',
  'ippo',
  'mappo',
] as const

export type BackendControlMode = typeof SUPPORTED_BACKEND_CONTROL_MODES[number]

export const IPPO_SUPPORTED_PERIODS = ['off_peak'] as const

export function controlModePeriodCompatibility(
  mode: string,
  period: string,
): { compatible: boolean; reason: string } {
  if (mode !== 'ippo' || IPPO_SUPPORTED_PERIODS.includes(period as 'off_peak')) {
    return { compatible: true, reason: '' }
  }
  return {
    compatible: false,
    reason: '当前 IPPO 模型仅兼容平峰拓扑，请选择平峰或改用其他算法',
  }
}

export const CONTROL_MODE_LABELS: Record<string, string> = {
  fixed: '固定配时',
  max_pressure: 'Max Pressure',
  sotl: 'SOTL',
  ippo: 'IPPO',
  mappo: 'MAPPO',
}

export const DASHBOARD_CONTROL_MODES = [
  { value: 'fixed', label: CONTROL_MODE_LABELS.fixed, backendSupported: true },
  { value: 'max_pressure', label: CONTROL_MODE_LABELS.max_pressure, backendSupported: true },
  { value: 'sotl', label: CONTROL_MODE_LABELS.sotl, backendSupported: true },
  { value: 'ippo', label: CONTROL_MODE_LABELS.ippo, backendSupported: true },
  { value: 'mappo', label: CONTROL_MODE_LABELS.mappo, backendSupported: true },
] as const

export function resolveCatalogControlModes(controlModes: string[] | null | undefined): string[] {
  return controlModes == null ? [...SUPPORTED_BACKEND_CONTROL_MODES] : [...controlModes]
}

export function resolveDashboardControlModes(controlModes: string[]) {
  const catalogModes = new Set(controlModes)
  return DASHBOARD_CONTROL_MODES.filter((item) => catalogModes.has(item.value))
}

export function isBackendControlMode(mode: string): mode is BackendControlMode {
  return SUPPORTED_BACKEND_CONTROL_MODES.some((value) => value === mode)
}

export function requireAvailableControlMode(
  mode: string,
  catalogControlModes: string[],
): BackendControlMode {
  if (!isBackendControlMode(mode) || !catalogControlModes.includes(mode)) {
    throw new Error(`后端未提供管控算法：${mode}`)
  }
  return mode
}

export function requirePeriodCompatibleControlMode(
  mode: BackendControlMode,
  period: string,
): BackendControlMode {
  const compatibility = controlModePeriodCompatibility(mode, period)
  if (!compatibility.compatible) throw new Error(compatibility.reason)
  return mode
}

export function resolveControlModeLabel(mode: string): string {
  return CONTROL_MODE_LABELS[mode] ?? mode
}
