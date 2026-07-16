export type TrafficStatus = 'free' | 'slow' | 'congested'

export interface TrafficIntersection {
  intersection_id: string
  name: string
  x: number
  y: number
  current_phase: number
  phase_name: string
  phase_duration: number
  queue_length: number
  avg_waiting_time: number
  avg_speed: number
  status: TrafficStatus | string
}

export interface TrafficLane {
  lane_id: string
  edge_id: string
  vehicle_count: number
  queue_length: number
  avg_speed: number
  occupancy: number
  status: TrafficStatus | string
}

export interface TrafficVehicle {
  vehicle_id: string
  x: number
  y: number
  speed: number
  waiting_time: number
  lane_id: string
  type: string
  angle?: number
}

export interface TrafficStateSnapshot {
  run_id: string
  sim_time: number
  intersections: TrafficIntersection[]
  lanes: TrafficLane[]
  vehicles: TrafficVehicle[]
}

export interface TrafficStateWsMessage {
  type: 'traffic_state'
  timestamp: number
  data: {
    vehicle_count?: number
    avg_speed?: number
    intersections?: TrafficIntersection[]
    lanes?: TrafficLane[]
    vehicles?: TrafficVehicle[]
  }
}

export interface TrafficSummary {
  vehicle_count: number | null
  avg_speed: number | null
}
