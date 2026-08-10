export type AlgorithmType = 'baseline' | 'rule_based' | 'reinforcement_learning' | string

export interface AlgorithmDefinition {
  algorithm_id: string
  name: string
  type: AlgorithmType
  description: string
}

export interface AlgorithmsResponse {
  algorithms: AlgorithmDefinition[]
}

export interface SwitchAlgorithmRequest {
  algorithm_id: string
  parameters: {
    min_green: number
    max_green: number
  }
}

export interface SwitchAlgorithmResponse {
  run_id: string
  algorithm: string
  status: string
}

export interface AlgorithmParameters {
  min_green: number
  max_green: number
}

export interface AlgorithmMetrics {
  avg_speed: number
  avg_waiting_time: number
  avg_queue_length: number
  congested_intersections: number
}

export interface AlgorithmComparisonRow {
  key: keyof AlgorithmMetrics
  label: string
  unit: string
  current: number | null
  baseline: number | null
  delta: number | null
  improved: boolean | null
}
