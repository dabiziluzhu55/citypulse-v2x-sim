export interface CloudAdvice {
  strategy: string
  target_area: string
  reason: string
  algorithm: string
}

export interface EdgeLocalState {
  queue_length: number
  avg_waiting_time: number
  current_phase: number
}

export interface EdgeRuleCheck {
  min_green_satisfied: boolean
  conflict_free: boolean
}

export interface EdgeLastAction {
  action_type: string
  target_phase: number
  duration: number
}

export interface EdgeAgent {
  edge_agent_id: string
  intersection_id: string
  local_state: EdgeLocalState
  local_rule_check: EdgeRuleCheck
  last_action: EdgeLastAction
  status: string
}

export interface VehicleAdvice {
  type: string
  recommended_speed?: number
  recommended_path?: string
}

export interface CollaborationVehicle {
  vehicle_id: string
  lane_id: string
  speed: number
  waiting_time?: number
  received_advice: VehicleAdvice
}

export interface CollaborationStateSnapshot {
  run_id: string
  sim_time: number
  cloud: CloudAdvice
  edges: EdgeAgent[]
  vehicles: CollaborationVehicle[]
}

export interface CollaborationStateWsMessage {
  type: 'collaboration_state'
  timestamp: number
  data: {
    cloud?: Partial<CloudAdvice>
    edges?: EdgeAgent[]
    vehicles?: CollaborationVehicle[]
  }
}

export interface CollaborationLogEntry {
  id: string
  timeLabel: string
  source: string
  message: string
}
