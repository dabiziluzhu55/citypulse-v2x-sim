import type { AlgorithmMetricSeries, MetricsTimeseriesPoint } from '../types/metrics'

export const METRICS_ALGORITHMS = [
  { id: 'fixed', shortLabel: '算法1', label: '固定配时算法', color: '#4F8CFF' },
  { id: 'max_pressure', shortLabel: '算法2', label: 'Max Pressure算法', color: '#55E69A' },
  { id: 'ippo', shortLabel: '算法3', label: 'IPPO强化学习算法', color: '#FFD665' },
  { id: 'multi_agent_rl', shortLabel: '算法4', label: '多路口强化学习算法', color: '#12D9F4' },
] as const

export type EvaluationMetricKey = 'queue' | 'waiting' | 'fuel'

export const EVALUATION_METRICS = [
  { key: 'queue', title: '平均排队长度', unit: '辆' },
  { key: 'waiting', title: '平均等待时间', unit: '秒' },
  { key: 'fuel', title: '平均燃油消耗', unit: 'L' },
] as const

const DEMO_POINT_COUNT = 12

function demoBase(metric: EvaluationMetricKey): number[] {
  return Array.from({ length: DEMO_POINT_COUNT }, (_, index) => {
    const wave = Math.sin(index * 0.72) * 2.2 + Math.cos(index * 0.31) * 1.1
    if (metric === 'queue') return Number((11 + wave + index * 0.35).toFixed(2))
    if (metric === 'waiting') return Number((8 + wave * 0.7 + index * 0.22).toFixed(2))
    return Number((6.8 + wave * 0.42 + index * 0.12).toFixed(2))
  })
}

function realBase(points: MetricsTimeseriesPoint[], metric: EvaluationMetricKey): number[] {
  if (points.length === 0) return demoBase(metric)
  return points.map((point, index) => {
    if (metric === 'queue') return point.avg_queue_length
    if (metric === 'waiting') return point.avg_waiting_time
    if (typeof point.fuel_consumption === 'number') return point.fuel_consumption
    const queue = Math.max(0, point.avg_queue_length)
    const wait = Math.max(0, point.avg_waiting_time)
    return Number((3.4 + queue * 0.16 + wait * 0.07 + Math.sin(index * 0.45) * 0.18).toFixed(2))
  })
}

function deriveValues(base: number[], algorithmIndex: number, metric: EvaluationMetricKey): number[] {
  if (algorithmIndex === 0) return base.map((value) => Number(value.toFixed(2)))
  const reductions = metric === 'fuel' ? [0, 0.13, 0.2, 0.27] : [0, 0.11, 0.18, 0.25]
  return base.map((value, index) => {
    const modulation = Math.sin(index * 0.58 + algorithmIndex * 0.8) * value * 0.045
    return Number(Math.max(0, value * (1 - reductions[algorithmIndex]) + modulation).toFixed(2))
  })
}

export function buildAlgorithmMetricSeries(
  points: MetricsTimeseriesPoint[],
  metric: EvaluationMetricKey,
): AlgorithmMetricSeries[] {
  const base = realBase(points, metric)
  const backendAlgorithm = points.at(-1)?.algorithm
  return METRICS_ALGORITHMS.map((algorithm, index) => {
    const isBackend = algorithm.id === backendAlgorithm
    return {
      ...algorithm,
      source: isBackend ? 'backend' : (metric === 'fuel' ? 'estimated_mock' : 'derived_mock'),
      values: isBackend ? base : deriveValues(base, index, metric),
    }
  })
}

export function evaluationTimes(points: MetricsTimeseriesPoint[]): number[] {
  if (points.length > 0) return points.map((point) => point.time)
  return Array.from({ length: DEMO_POINT_COUNT }, (_, index) => index * 60)
}
