export const ACTIVE_SESSION_ID_KEY = 'citypulse.active_session_id'

export const STATUS_POLL_INTERVAL_MS = 2_000

export const DEFAULT_INTERSECTION_ID = 'demo_2'

export const CONTROL_MODE_LABELS: Record<string, string> = {
  fixed: '固定配时算法',
  max_pressure: 'Max Pressure算法',
  ippo: 'IPPO强化学习算法',
  multi_agent_rl: '多路口强化学习算法',
}

export const DASHBOARD_CONTROL_MODES = [
  { value: 'fixed', label: CONTROL_MODE_LABELS.fixed, backendSupported: true },
  { value: 'max_pressure', label: CONTROL_MODE_LABELS.max_pressure, backendSupported: true },
  { value: 'ippo', label: CONTROL_MODE_LABELS.ippo, backendSupported: false },
  { value: 'multi_agent_rl', label: CONTROL_MODE_LABELS.multi_agent_rl, backendSupported: false },
] as const

export function isBackendControlMode(mode: string): boolean {
  return DASHBOARD_CONTROL_MODES.some((item) => item.value === mode && item.backendSupported)
}

export function resolveControlModeLabel(mode: string): string {
  return CONTROL_MODE_LABELS[mode] ?? mode
}
