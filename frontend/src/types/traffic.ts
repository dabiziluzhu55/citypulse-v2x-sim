import type {
  SimulationMetrics,
  SimulationSnapshot,
  SimulationVehicle,
} from './simulation'

export type TrafficStatus = 'free' | 'slow' | 'congested'

export interface TrafficIntersectionView {
  intersection_id: string
  name: string
  current_phase: number
  phase_name: string
  stage_elapsed: number
  queue_length: number
  vehicle_count: number
  avg_waiting_time: number
  avg_speed: number
  status: TrafficStatus
}

export interface TrafficVehicleView {
  vehicle_id: string
  longitude: number | null
  latitude: number | null
  x: number
  y: number
  speed: number
  angle: number
  lane_id: string
}

export interface TrafficStateView {
  session_id: string
  elapsed_seconds: number
  duration_seconds: number
  progress: number
  official_time: string
  intersections: TrafficIntersectionView[]
  vehicles: TrafficVehicleView[]
  metrics: SimulationMetrics | null
}

export interface TrafficSummary {
  vehicle_count: number | null
  avg_speed: number | null
}

export type { SimulationSnapshot, SimulationVehicle }
