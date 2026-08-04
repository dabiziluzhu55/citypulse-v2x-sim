export type EventType =
  | 'congestion'
  | 'abnormal_parking'
  | 'lane_closure'
  | 'queue_spillover'
  | string

export type EventLevel = 'low' | 'medium' | 'high'

export interface EventLocation {
  intersection_id?: string
  lane_id?: string
  edge_id?: string
}

export interface EventEvidence {
  avg_speed?: number
  queue_length?: number
  avg_waiting_time?: number
}

export interface TrafficEvent {
  event_id: string
  time: number
  type: EventType
  level: EventLevel
  location: EventLocation
  description: string
  evidence?: EventEvidence
  suggestion?: string
}

export interface EventsResponse {
  events: TrafficEvent[]
}

export interface EventDetectedWsMessage {
  type: 'event_detected'
  timestamp: number
  data: {
    event_id: string
    type: EventType
    level: EventLevel
    location: EventLocation
    description?: string
    evidence?: EventEvidence
    suggestion?: string
    time?: number
  }
}

export interface PredictionPoint {
  time_offset: number
  predicted_flow: number
  predicted_queue: number
  congestion_risk: number
}

export interface PredictionResponse {
  target: string
  horizon: number
  predictions: PredictionPoint[]
  model: string
  updated_at: number
}
