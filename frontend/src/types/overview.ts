export type SimulationStatus = 'idle' | 'running' | 'paused' | 'stopped' | 'error'

export interface RunOverview {
  run_id: string
  scenario_id: string
  scenario_name: string
  status: SimulationStatus
  sim_time: number
  vehicle_count: number
  active_vehicle_count: number
  algorithm: string
  cloud_edge_enabled: boolean
  avg_speed: number
  avg_waiting_time: number
  avg_queue_length: number
  congested_intersections: number
}

export interface RunOverviewWsMessage {
  type: 'overview'
  timestamp?: number
  data: RunOverview
}
