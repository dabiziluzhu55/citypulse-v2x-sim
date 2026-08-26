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
  stage: string
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
  road_id: string
  lane_id: string
  type_id?: string
  acceleration?: number
  lane_index?: number
  lane_position?: number
  allowed_speed?: number
  route_id?: string
  route_index?: number
  distance?: number
  next_intersection_id?: string | null
  target_speed?: number
  target_lane_index?: number
  canonical_segment_id?: string
  canonical_route_evidence?: 'same_lane' | 'lane_change' | 'unique_connection' | 'authoritative_endpoint'
  canonical_heading_radians?: number
  canonical_source_x?: number
  canonical_source_y?: number
  canonical_lane_station?: number
  canonical_motion_resolved?: boolean
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
