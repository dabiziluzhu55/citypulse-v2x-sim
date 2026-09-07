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
    key: 'path_speed',
    label: '平均行程速度',
    field: 'path_avg_speed_kmh',
    lowerBetter: false,
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

/** CoV2X live presentation only: values below this round to 0.0% and are not held. */
export const POSITIVE_DISPLAY_EPSILON = 0.05

export type PositiveAdvantageCache = Record<string, AdvantageMetric | null>

export function createEmptyPositiveAdvantageCache(): PositiveAdvantageCache {
  return Object.fromEntries(
    ADVANTAGE_METRIC_SPECS.map((spec) => [spec.key, null]),
  ) as PositiveAdvantageCache
}

export function isPositiveAdvantage(
  metric: Pick<AdvantageMetric, 'value' | 'improved'> | null | undefined,
  epsilon = POSITIVE_DISPLAY_EPSILON,
): boolean {
  return (
    !!metric
    && typeof metric.value === 'number'
    && Number.isFinite(metric.value)
    && metric.value >= epsilon
    && metric.improved === true
  )
}

export function positiveAdvantageCacheScope(input: {
  runId: string
  algorithmId: string
  contractFingerprint: string
  cov2xSessionId: string
  fixedSessionId: string
  timeseriesEmpty: boolean
}): string {
  return [
    input.runId,
    input.algorithmId,
    input.contractFingerprint,
    input.cov2xSessionId,
    input.fixedSessionId,
    input.timeseriesEmpty ? 'empty' : 'data',
  ].join('|')
}

export function updatePositiveAdvantageCache(
  cache: PositiveAdvantageCache,
  metrics: readonly AdvantageMetric[],
  options: { algorithmId: string; finished: boolean },
): PositiveAdvantageCache {
  if (options.algorithmId !== 'cov2x') return createEmptyPositiveAdvantageCache()
  const next: PositiveAdvantageCache = { ...cache }
  for (const metric of metrics) {
    if (options.finished) {
      next[metric.key] = isPositiveAdvantage(metric) ? { ...metric } : null
      continue
    }
    if (isPositiveAdvantage(metric)) next[metric.key] = { ...metric }
  }
  return next
}

export function resolveDisplayedAdvantageMetric(
  current: AdvantageMetric,
  previousPositive: AdvantageMetric | null | undefined,
  options: { algorithmId: string; finished: boolean },
): AdvantageMetric {
  if (options.algorithmId !== 'cov2x' || options.finished) return current
  if (isPositiveAdvantage(current)) return current
  if (isPositiveAdvantage(previousPositive)) return previousPositive as AdvantageMetric
  return { ...current, value: null, direction: null, improved: null }
}

export function resolveDisplayedAdvantageMetrics(
  metrics: readonly AdvantageMetric[],
  cache: PositiveAdvantageCache,
  options: { algorithmId: string; finished: boolean },
): AdvantageMetric[] {
  return metrics.map((metric) => (
    resolveDisplayedAdvantageMetric(metric, cache[metric.key], options)
  ))
}
