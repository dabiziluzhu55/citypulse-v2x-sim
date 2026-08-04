import type { BackendControlMode } from '../constants/simulationOptions'

export type SimulationState =
  | 'STARTING'
  | 'RUNNING'
  | 'PAUSED'
  | 'STOPPING'
  | 'STOPPED'
  | 'COMPLETED'
  | 'FAILED'

export interface DisturbanceEventPayload {
  event_type: 'lane_closure' | 'speed_limit' | 'accident'
  event_id: string
  start_seconds: number
  end_seconds: number
  lane_ids?: string[]
  lane_id?: string
  max_speed?: number
  position_ratio?: number
}

interface DisturbanceTargetBase {
  intersection_id: string
  event_id?: string
  start_seconds: number
  end_seconds: number
}

export interface LaneClosureDisturbanceTarget extends DisturbanceTargetBase {
  event_type: 'lane_closure'
  lane_ids?: string[]
}

export interface SpeedLimitDisturbanceTarget extends DisturbanceTargetBase {
  event_type: 'speed_limit'
  lane_ids?: string[]
  max_speed?: number
}

export interface AccidentDisturbanceTarget extends DisturbanceTargetBase {
  event_type: 'accident'
  lane_id?: string
  position_ratio?: number
}

export type DisturbanceTargetPayload =
  | LaneClosureDisturbanceTarget
  | SpeedLimitDisturbanceTarget
  | AccidentDisturbanceTarget

export interface StartSimulationRequest {
  scenario_preset_id: string
  period: string
  origins: Record<string, string[]>
  window_start_seconds: number
  duration_seconds: number
  control_mode: BackendControlMode
  seed: number
  step_length: number
  realtime: boolean
  gui: boolean
  snapshot_interval_seconds: number
  disturbance_targets: DisturbanceTargetPayload[]
  playback_speed: number | null
}

export interface StartSimulationResponse {
  session_id: string
  state: SimulationState
  status_url: string
  websocket_url: string
  metrics_url: string | null
  scenario_preset_id: string | null
}

export interface StopSimulationResponse {
  session_id: string
  state: SimulationState
}

export interface SimulationLaneRuntime {
  vehicle_count: number
  halting_count: number
  mean_speed: number
  waiting_time: number
  occupancy: number
}

export interface SimulationIntersectionRuntime {
  current_phase: number
  pending_phase: number | null
  stage: string
  stage_elapsed: number
  lanes: Record<string, SimulationLaneRuntime>
}

export interface SimulationVehicle {
  vehicle_id: string
  x: number
  y: number
  longitude: number | null
  latitude: number | null
  speed: number
  angle: number
  height: number
  road_id: string
  lane_id: string
  type_id?: string
  lane_position?: number
}

export interface SimulationPlaybackResponse {
  session_id: string
  state: SimulationState
  playback_speed: number | null
}

export interface SimulationMetrics {
  active_vehicles: number
  departed_vehicles: number
  arrived_vehicles: number
  remaining_vehicles: number
  halting_vehicles: number
  total_waiting_time: number
  mean_speed: number
  avg_waiting_time?: number
  avg_travel_time?: number
  avg_queue_length?: number
  throughput?: number
  fuel_consumption?: number
  evaluation?: SimulationEvaluation
}

export interface SimulationEvaluation {
  episode_id: string
  algorithm: string
  avg_waiting_time: number
  avg_travel_time: number
  avg_queue_length: number
  throughput: number
  fuel_consumption: number
  avg_decision_latency_ms: number
  departed: number
  arrived: number
  finished: boolean
}

export interface SimulationEvent {
  event_id: string
  event_type: string
  state?: string
  start_seconds?: number
  end_seconds?: number
  [key: string]: unknown
}

export interface SimulationSnapshot {
  session_id: string
  state: SimulationState
  sequence: number
  elapsed_seconds: number
  duration_seconds: number
  progress: number
  official_time: string
  playback_speed: number | null
  intersections: Record<string, SimulationIntersectionRuntime>
  vehicles: SimulationVehicle[]
  events: SimulationEvent[]
  metrics: SimulationMetrics
  evaluation?: SimulationEvaluation | null
  error: string | null
}

export interface SnapshotWsMessage {
  type: 'snapshot'
  data: SimulationSnapshot
}

export interface HeartbeatWsMessage {
  type: 'heartbeat'
  session_id: string
  timestamp: string
}

export type SimulationWsMessage = SnapshotWsMessage | HeartbeatWsMessage

export const TERMINAL_SIMULATION_STATES: SimulationState[] = [
  'STOPPED',
  'COMPLETED',
  'FAILED',
]
