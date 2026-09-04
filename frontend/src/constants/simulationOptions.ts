export const ACTIVE_SESSION_ID_KEY = 'citypulse.active_session_id'
export const ACTIVE_SIMULATION_CONTEXT_KEY = 'citypulse.active_simulation_context'

export const STATUS_POLL_INTERVAL_MS = 2_000
// A stable one-second authoritative frame is preferable to missing a 500 ms
// deadline for full-network payloads. The presentation timeline interpolates
// between these frames; lower this only after stream P95 stays below 250 ms.
export const SIMULATION_SNAPSHOT_INTERVAL_MS = 1_000

export const DEFAULT_INTERSECTION_ID = 'demo_2'

export const SUPPORTED_BACKEND_CONTROL_MODES = [
  'fixed',
  'max_pressure',
  'sotl',
  'ippo',
  'mappo',
  'cov2x',
] as const

export type BackendControlMode = typeof SUPPORTED_BACKEND_CONTROL_MODES[number]

export function controlModePeriodCompatibility(
  _mode: string,
  _period: string,
): { compatible: boolean; reason: string } {
  return { compatible: true, reason: '' }
}

export const CONTROL_MODE_LABELS: Record<string, string> = {
  fixed: '固定配时',
  max_pressure: 'Max Pressure',
  sotl: 'SOTL',
  ippo: 'IPPO',
  mappo: 'MAPPO',
  cov2x: 'CoV2X 车路云协同',
}

export const DASHBOARD_CONTROL_MODES = [
  { value: 'fixed', label: CONTROL_MODE_LABELS.fixed, backendSupported: true },
  { value: 'max_pressure', label: CONTROL_MODE_LABELS.max_pressure, backendSupported: true },
  { value: 'sotl', label: CONTROL_MODE_LABELS.sotl, backendSupported: true },
  { value: 'ippo', label: CONTROL_MODE_LABELS.ippo, backendSupported: true },
  { value: 'mappo', label: CONTROL_MODE_LABELS.mappo, backendSupported: true },
  { value: 'cov2x', label: CONTROL_MODE_LABELS.cov2x, backendSupported: true },
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
