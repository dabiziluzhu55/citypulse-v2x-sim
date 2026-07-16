import type { ControlCommand } from '../types/simulation'

export const ACTIVE_RUN_ID_KEY = 'citypulse.active_run_id'

export const ALGORITHM_OPTIONS = [
  { label: '固定配时', value: 'fixed_time' },
  { label: 'MaxPressure', value: 'max_pressure' },
  { label: 'IPPO', value: 'ippo' },
] as const

export const STATUS_POLL_INTERVAL_MS = 2_000

export interface ControlButtonConfig {
  label: string
  command: ControlCommand
}

export const CONTROL_BUTTONS: ControlButtonConfig[] = [
  { label: '暂停', command: 'pause' },
  { label: '继续', command: 'resume' },
  { label: '停止', command: 'stop' },
  { label: '重置', command: 'reset' },
  { label: '单步', command: 'step' },
]
