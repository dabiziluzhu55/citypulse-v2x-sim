import type { RealtimeMetrics } from '../types/metrics'

export const METRICS_POLL_INTERVAL_MS = 5_000
export const COMPARISON_POLL_INTERVAL_MS = 30_000

export const REALTIME_METRIC_ITEMS: Array<{
  key: keyof RealtimeMetrics
  label: string
  unit: string
  fractionDigits?: number
}> = [
  { key: 'avg_speed', label: '平均速度', unit: 'm/s' },
  { key: 'avg_waiting_time', label: '平均等待时间', unit: 's' },
  { key: 'avg_travel_time', label: '平均行程时间', unit: 's' },
  { key: 'avg_queue_length', label: '平均排队长度', unit: 'veh' },
  { key: 'throughput', label: '当前通行量', unit: 'veh/h' },
  { key: 'fuel_consumption', label: '燃油消耗', unit: '%', fractionDigits: 1 },
  { key: 'co2_emission', label: 'CO₂ 排放', unit: '%', fractionDigits: 1 },
]

export const ALGORITHM_LABELS: Record<string, string> = {
  fixed_time: '固定配时',
  actuated: '感应控制',
  max_pressure: 'Max-Pressure',
  ippo: 'IPPO',
}

export function formatAlgorithmLabel(algorithmId: string): string {
  return ALGORITHM_LABELS[algorithmId] ?? algorithmId
}
