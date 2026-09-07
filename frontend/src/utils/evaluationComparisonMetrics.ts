import { EVALUATION_BUCKET_SECONDS } from '../composables/useEvaluationComparison.ts'
import type { MetricsTimeseriesPoint } from '../types/metrics'
import type { SimulationState } from '../types/simulation.ts'

export const TRAFFIC_STATE_COLORS = {
  畅通: '#008000',
  基本畅通: '#99CC00',
  轻度拥堵: '#FFFF00',
  中度拥堵: '#FF9900',
  严重拥堵: '#FF0000',
} as const

export type TrafficStateLabel = keyof typeof TRAFFIC_STATE_COLORS

export const ADVANTAGE_METRIC_SPECS = [
  {
    key: 'max_queue',
    label: '最大排队长度',
    field: 'regional_max_queue_length_m',
    lowerBetter: true,
  },
  {
    key: 'waiting_time',
    label: '平均等待时间',
    field: 'avg_waiting_time',
    lowerBetter: true,
  },
  {
    key: 'throughput',
    label: '吞吐流率',
    field: 'throughput',
    lowerBetter: false,
  },
  {
    key: 'fuel_intensity',
    label: '油耗强度',
    field: 'fuel_intensity_L_per_100km',
    lowerBetter: true,
  },
] as const

export interface AdvantageMetric {
  key: string
  label: string
  value: number | null
  direction: 'up' | 'down' | null
  improved: boolean | null
}

function pointField(
  point: MetricsTimeseriesPoint | null | undefined,
  field: (typeof ADVANTAGE_METRIC_SPECS)[number]['field'],
): number | null {
  if (!point) return null
  const value = point[field]
  if (field === 'fuel_intensity_L_per_100km') {
    if (typeof point.fuel_intensity_L_per_100km === 'number') return point.fuel_intensity_L_per_100km
    if (typeof point.fuel_consumption === 'number') return point.fuel_consumption
  }
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function hasFinishedFixedBaseline(
  points: readonly MetricsTimeseriesPoint[],
): boolean {
  return points.some((point) => point.algorithm === 'fixed' && point.finished === true)
}

export function resolveComparedAlgorithm(
  points: readonly MetricsTimeseriesPoint[],
  activeAlgorithm: string,
): string | null {
  const current = activeAlgorithm.trim()
  if (current && current !== 'fixed') return current
  const latest = [...points]
    .reverse()
    .find((point) => point.algorithm && point.algorithm !== 'fixed')
  return latest?.algorithm ?? null
}

export function findComparableBaselinePoint(
  baselinePoints: readonly MetricsTimeseriesPoint[],
  currentTime: number,
  useFinal: boolean,
): MetricsTimeseriesPoint | null {
  if (baselinePoints.length === 0) return null
  if (useFinal) {
    return [...baselinePoints].reverse().find((point) => point.finished === true)
      ?? baselinePoints[baselinePoints.length - 1]
      ?? null
  }
  const bucket = Math.floor(currentTime / EVALUATION_BUCKET_SECONDS)
  const sameBucket = baselinePoints.filter((point) => (
    Math.floor(point.time / EVALUATION_BUCKET_SECONDS) === bucket
  ))
  if (sameBucket.length > 0) {
    return sameBucket.reduce((closest, point) => (
      Math.abs(point.time - currentTime) < Math.abs(closest.time - currentTime) ? point : closest
    ))
  }
  const previous = baselinePoints.filter((point) => point.time <= currentTime)
  if (previous.length === 0) return null
  return previous.reduce((closest, point) => (
    currentTime - point.time < currentTime - closest.time ? point : closest
  ))
}

export function calculateImprovement(
  currentValue: number | null | undefined,
  fixedValue: number | null | undefined,
  lowerBetter: boolean,
): Pick<AdvantageMetric, 'value' | 'direction' | 'improved'> {
  if (
    typeof currentValue !== 'number'
    || typeof fixedValue !== 'number'
    || !Number.isFinite(currentValue)
    || !Number.isFinite(fixedValue)
    || fixedValue === 0
  ) {
    return { value: null, direction: null, improved: null }
  }
  const raw = lowerBetter
    ? (fixedValue - currentValue) / fixedValue * 100
    : (currentValue - fixedValue) / fixedValue * 100
  if (!Number.isFinite(raw)) return { value: null, direction: null, improved: null }
  const direction = currentValue > fixedValue
    ? 'up'
    : currentValue < fixedValue
      ? 'down'
      : null
  const improved = raw > 0 ? true : raw < 0 ? false : null
  return { value: raw, direction, improved }
}

export function buildAdvantageMetric(
  spec: (typeof ADVANTAGE_METRIC_SPECS)[number],
  currentPoint: MetricsTimeseriesPoint | null,
  baselinePoint: MetricsTimeseriesPoint | null,
): AdvantageMetric {
  const improvement = calculateImprovement(
    pointField(currentPoint, spec.field),
    pointField(baselinePoint, spec.field),
    spec.lowerBetter,
  )
  return {
    key: spec.key,
    label: spec.label,
    ...improvement,
  }
}

export function buildAdvantageMetrics(
  points: readonly MetricsTimeseriesPoint[],
  activeAlgorithm: string,
  _simulationState?: SimulationState | string | null,
): AdvantageMetric[] {
  const empty = ADVANTAGE_METRIC_SPECS.map((spec) => ({
    key: spec.key,
    label: spec.label,
    value: null,
    direction: null,
    improved: null,
  }))
  if (!hasFinishedFixedBaseline(points)) return empty
  const compared = resolveComparedAlgorithm(points, activeAlgorithm)
  if (!compared) return empty
  const currentPoints = points.filter((point) => point.algorithm === compared)
  const currentPoint = currentPoints.at(-1) ?? null
  if (!currentPoint) return empty
  const useFinal = currentPoint.finished === true
  const baselinePoint = findComparableBaselinePoint(
    points.filter((point) => point.algorithm === 'fixed'),
    currentPoint.time,
    useFinal,
  )
  return ADVANTAGE_METRIC_SPECS.map((spec) => (
    buildAdvantageMetric(spec, currentPoint, baselinePoint)
  ))
}

export function trafficStateColor(state: string | null | undefined): string | null {
  if (!state) return null
  return TRAFFIC_STATE_COLORS[state as TrafficStateLabel] ?? null
}

export function formatActiveVehicleCount(count: number | null | undefined): string {
  return typeof count === 'number' && Number.isFinite(count) ? String(count) : '—'
}

export function formatAdvantagePercent(value: number | null): string {
  return typeof value === 'number' ? `${Math.abs(value).toFixed(1)}%` : '—'
}
