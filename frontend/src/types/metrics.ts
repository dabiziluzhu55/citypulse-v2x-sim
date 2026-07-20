export interface RealtimeMetrics {
  avg_speed: number
  avg_waiting_time: number
  avg_travel_time: number
  avg_queue_length: number
  throughput: number
  fuel_consumption: number
  co2_emission: number
}

export interface RealtimeMetricsResponse {
  run_id: string
  time: number
  metrics: RealtimeMetrics
}

export interface AlgorithmResultMetrics {
  algorithm: string
  avg_waiting_time: number
  avg_travel_time: number
  avg_queue_length: number
  throughput: number
  fuel_consumption: number
}

export interface ExperimentComparisonResponse {
  experiment_id: string
  scenario_id: string
  baselines: string[]
  results: AlgorithmResultMetrics[]
}

export interface MetricsTimeseriesPoint {
  time: number
  algorithm?: string
  avg_waiting_time: number
  avg_travel_time?: number
  avg_queue_length: number
  throughput: number
  fuel_consumption?: number
}

export type MetricSeriesSource = 'backend' | 'derived_mock' | 'estimated_mock'

export interface AlgorithmMetricSeries {
  id: string
  shortLabel: string
  label: string
  color: string
  source: MetricSeriesSource
  values: number[]
}

export interface MetricsTimeseriesResponse {
  run_id: string
  series: MetricsTimeseriesPoint[]
}

export interface MetricComparisonRow {
  key: string
  label: string
  baselineLabel: string
  currentLabel: string
  baselineValue: number
  currentValue: number
  baselineDisplay: string
  currentDisplay: string
  improvementRate: number | null
  improvementDisplay: string
  improved: boolean | null
}
