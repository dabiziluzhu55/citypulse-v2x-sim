// 算法事件识别与短时预测的前端类型与扰动events无关

export type DetectedTrafficState =
  | 'localized_blockage'
  | 'spillback'
  | 'unknown_abnormal'
  | 'capacity_drop'
  | 'normal'
  | string

export type CongestionLevel = 'free' | 'slow' | 'congested' | 'severe'

export interface DetectedEventCard {
  event_id: string
  status: 'active' | 'ended' | string
  traffic_state: DetectedTrafficState
  display_type: string
  display_label: string
  severity: 'low' | 'medium' | 'high' | string
  confidence: number
  intersection_id: string
  lane_ids: string[]
  edge_id?: string
  approach_id?: string
  longitude: number | null
  latitude: number | null
  start_seconds: number
  end_seconds: number | null
  duration_seconds: number
  evidence: string[]
  suggestion: string
  cause?: string
  cause_confidence?: number
  prediction_summary: string
  event_type?: string | null
}

export interface EventDetectionPayload {
  as_of_seconds: number
  cards: DetectedEventCard[]
}

export interface IntersectionPrediction {
  current_vehicle_count: number
  predicted_vehicle_count: number
  delta: number
  delta_ratio: number | null
}

export interface PredictionPayload {
  horizon_seconds: number
  as_of_seconds: number
  model: string
  model_version?: string
  ready: boolean
  fallback?: boolean
  fallback_reason?: string
  inference_latency_ms?: number | null
  intersections: Record<string, IntersectionPrediction>
}

export interface EdgeTrafficStyle {
  level: CongestionLevel | string
  score: number
  mean_speed: number
  /** 占有率百分数，口径0～100 */
  occupancy_pct: number
  /** 兼容字段，与occupancy_pct同值 */
  occupancy?: number
  vehicle_count: number
  halting_count: number
}

export interface TrafficStylePayload {
  as_of_seconds: number
  edges: Record<string, EdgeTrafficStyle>
}

export interface IntelligencePayload {
  event_detection: EventDetectionPayload
  prediction: PredictionPayload
  traffic_style: TrafficStylePayload
}
