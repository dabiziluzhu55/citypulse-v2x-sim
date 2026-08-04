import type { AlgorithmMetricSeries, MetricsTimeseriesPoint } from '../types/metrics'

export const METRICS_ALGORITHMS = [
  { id: 'fixed', shortLabel: '算法1', label: '固定配时算法', color: '#4F8CFF' },
  { id: 'max_pressure', shortLabel: '算法2', label: 'Max Pressure算法', color: '#55E69A' },
  { id: 'sotl', shortLabel: '算法3', label: 'SOTL自组织信号算法', color: '#FFD665' },
] as const

export type EvaluationMetricKey = 'queue' | 'waiting' | 'fuel'

export const EVALUATION_METRICS = [
  { key: 'queue', title: '平均排队长度', unit: '辆' },
  { key: 'waiting', title: '平均等待时间', unit: '秒' },
  { key: 'fuel', title: '平均燃油消耗', unit: 'L' },
] as const

function timeKey(value: number): string {
  return value.toFixed(6)
}

function metricValue(
  point: MetricsTimeseriesPoint,
  metric: EvaluationMetricKey,
): number | null {
  if (metric === 'queue') return point.avg_queue_length
  if (metric === 'waiting') return point.avg_waiting_time
  return typeof point.fuel_consumption === 'number' ? point.fuel_consumption : null
}

export function evaluationTimes(points: MetricsTimeseriesPoint[]): number[] {
  return [...new Map(points.map((point) => [timeKey(point.time), point.time])).values()]
    .sort((left, right) => left - right)
}

export function buildAlgorithmMetricSeries(
  points: MetricsTimeseriesPoint[],
  metric: EvaluationMetricKey,
): AlgorithmMetricSeries[] {
  const times = evaluationTimes(points)
  return METRICS_ALGORITHMS.map((algorithm) => {
    const algorithmPoints = new Map(
      points
        .filter((point) => point.algorithm === algorithm.id)
        .map((point) => [timeKey(point.time), metricValue(point, metric)]),
    )
    const hasValues = [...algorithmPoints.values()].some((value) => value !== null)
    return {
      ...algorithm,
      source: hasValues ? 'backend' : 'missing',
      values: times.map((time) => algorithmPoints.get(timeKey(time)) ?? null),
    }
  })
}
