export const ACTIVE_SESSION_ID_KEY = 'citypulse.active_session_id'

export const STATUS_POLL_INTERVAL_MS = 2_000

export const DEFAULT_INTERSECTION_ID = 'demo_2'

export const CONTROL_MODE_LABELS: Record<string, string> = {
  fixed: '固定配时',
}

export function resolveControlModeLabel(mode: string): string {
  return CONTROL_MODE_LABELS[mode] ?? mode
}
