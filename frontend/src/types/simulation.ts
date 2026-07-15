export type RunLifecycleStatus =
  | 'starting'
  | 'running'
  | 'paused'
  | 'stopped'
  | 'idle'
  | 'error'

export type ControlCommand = 'pause' | 'resume' | 'stop' | 'reset' | 'step'

export interface StartRunRequest {
  scenario_id: string
  algorithm: string
  cloud_edge_enabled: boolean
  realtime: boolean
  step_length: number
}

export interface StartRunResponse {
  run_id: string
  status: RunLifecycleStatus
  message: string
}

export interface ControlRunRequest {
  command: ControlCommand
}

export interface ControlRunResponse {
  run_id: string
  status: RunLifecycleStatus
}

export interface RunStatus {
  run_id: string
  status: RunLifecycleStatus
  sim_time: number
  step: number
  vehicle_count: number
  message: string
}
