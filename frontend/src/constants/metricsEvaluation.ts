import type {
  AlgorithmMetricSeries,
  MetricPresentationStatus,
  MetricsTimeseriesPoint,
} from '../types/metrics'

export const METRICS_ALGORITHMS = [
  { id: 'fixed', shortLabel: '固定配时', label: '固定配时算法', color: '#4F8CFF' },
  { id: 'max_pressure', shortLabel: 'Max Pressure', label: 'Max Pressure算法', color: '#55E69A' },
  { id: 'sotl', shortLabel: 'SOTL', label: 'SOTL自组织信号算法', color: '#FFD665' },
  { id: 'ippo', shortLabel: 'IPPO', label: 'IPPO强化学习算法', color: '#FF7CCB' },
  { id: 'mappo', shortLabel: 'MAPPO', label: 'MAPPO强化学习算法', color: '#B98CFF' },
] as const

export type EvaluationMetricKey = 'queue' | 'waiting' | 'fuel'

export const EVALUATION_AXIS = {
  minMinutes: 0,
  maxMinutes: 15,
  intervalMinutes: 3,
} as const

export const EVALUATION_METRICS = [
  { key: 'queue', title: '平均排队长度', unit: '辆/进口车道' },
  { key: 'waiting', title: '平均等待时间', unit: '秒' },
  { key: 'fuel', title: '燃油消耗', unit: 'L/100km' },
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

function metricStatus(
  point: MetricsTimeseriesPoint,
  metric: EvaluationMetricKey,
): MetricPresentationStatus {
  const explicit = point.metric_status?.[metric]
  if (explicit) return explicit
  const value = metricValue(point, metric)
  if (typeof value === 'number') return point.finished ? 'final' : 'provisional'
  return point.finished ? 'unavailable' : 'pending'
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
        .map((point) => [timeKey(point.time), {
          value: metricValue(point, metric),
          status: metricStatus(point, metric),
        }]),
    )
    const hasValues = [...algorithmPoints.values()].some((entry) => entry.value !== null)
    return {
      ...algorithm,
      source: hasValues ? 'backend' : 'missing',
      values: times.map((time) => algorithmPoints.get(timeKey(time))?.value ?? null),
      statuses: times.map((time) => algorithmPoints.get(timeKey(time))?.status ?? 'pending'),
    }
  })
}
