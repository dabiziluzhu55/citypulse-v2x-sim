import type {
  AlgorithmMetricSeries,
  EvaluationMetricKey,
  MetricPresentationStatus,
  MetricsTimeseriesPoint,
} from '../types/metrics'

export type { EvaluationMetricKey }

export const METRICS_ALGORITHMS = [
  { id: 'fixed', shortLabel: '固定配时', label: '固定配时算法', color: '#4F8CFF' },
  { id: 'max_pressure', shortLabel: 'Max Pressure', label: 'Max Pressure算法', color: '#55E69A' },
  { id: 'sotl', shortLabel: 'SOTL', label: 'SOTL自组织信号算法', color: '#FFD665' },
  { id: 'ippo', shortLabel: 'IPPO', label: 'IPPO强化学习算法', color: '#FF7CCB' },
  { id: 'mappo', shortLabel: 'MAPPO', label: 'MAPPO强化学习算法', color: '#B98CFF' },
  { id: 'cov2x', shortLabel: 'CoV2X', label: 'CoV2X车路云协同算法', color: '#FF8B38' },
] as const

export const EVALUATION_AXIS = {
  minMinutes: 0,
  maxMinutes: 15,
  intervalMinutes: 3,
} as const

const EVALUATION_AXIS_NICE_INTERVALS = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 3, 5, 10, 15] as const

export function evaluationAxisFromDurationSeconds(
  durationSeconds: number | null | undefined,
): { minMinutes: number; maxMinutes: number; intervalMinutes: number } {
  const seconds = typeof durationSeconds === 'number'
    && Number.isFinite(durationSeconds)
    && durationSeconds > 0
    ? durationSeconds
    : EVALUATION_AXIS.maxMinutes * 60
  const maxMinutes = seconds / 60
  const target = maxMinutes / 5
  const intervalMinutes = EVALUATION_AXIS_NICE_INTERVALS.find((value) => value + 1e-9 >= target)
    ?? Math.max(target, 0.1)
  return {
    minMinutes: 0,
    maxMinutes,
    intervalMinutes,
  }
}

export const EVALUATION_METRICS = [
  { key: 'path_speed', title: '平均行程速度', unit: 'km/h', field: 'path_avg_speed_kmh' },
  { key: 'stops', title: '平均停车次数', unit: '次/车', field: 'avg_stops_per_vehicle' },
  { key: 'max_queue', title: '最大排队长度', unit: 'm', field: 'regional_max_queue_length_m' },
  { key: 'travel_time', title: '平均行程时间', unit: 's', field: 'avg_travel_time' },
  { key: 'waiting_time', title: '平均等待时间', unit: 's', field: 'avg_waiting_time' },
  { key: 'throughput', title: '吞吐流率', unit: 'veh/h', field: 'throughput' },
  { key: 'spillback', title: '溢流率', unit: '%', field: 'spillback_rate' },
  { key: 'hard_braking', title: '急刹车率', unit: '次/100辆', field: 'hard_braking_rate' },
  { key: 'fuel_intensity', title: '百公里油耗强度', unit: 'L/100km', field: 'fuel_intensity_L_per_100km' },
] as const satisfies ReadonlyArray<{
  key: EvaluationMetricKey
  title: string
  unit: string
  field: keyof MetricsTimeseriesPoint
}>

function timeKey(value: number): string {
  return value.toFixed(6)
}

export function metricValue(
  point: MetricsTimeseriesPoint,
  metric: EvaluationMetricKey,
): number | null {
  if (metric === 'path_speed') return typeof point.path_avg_speed_kmh === 'number' ? point.path_avg_speed_kmh : null
  if (metric === 'stops') return typeof point.avg_stops_per_vehicle === 'number' ? point.avg_stops_per_vehicle : null
  if (metric === 'max_queue') {
    return typeof point.regional_max_queue_length_m === 'number' ? point.regional_max_queue_length_m : null
  }
  if (metric === 'travel_time') return typeof point.avg_travel_time === 'number' ? point.avg_travel_time : null
  if (metric === 'waiting_time') return point.avg_waiting_time
  if (metric === 'throughput') return point.throughput
  if (metric === 'spillback') return typeof point.spillback_rate === 'number' ? point.spillback_rate : null
  if (metric === 'hard_braking') return typeof point.hard_braking_rate === 'number' ? point.hard_braking_rate : null
  if (typeof point.fuel_intensity_L_per_100km === 'number') return point.fuel_intensity_L_per_100km
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
