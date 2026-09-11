/**
 * Frozen CityPulse-Qwen V2 closed-loop validation vs Fixed timing.
 * Seed 44001, 45 unseen disturbance scenarios. Not live counterfactual.
 */
export const CITYPULSE_QWEN_BENCHMARK = {
  model: 'CityPulse-Qwen V2 AWQ',
  baseline: 'Fixed',
  dataset: 'seed 44001',
  scenarios: 45,
  avgQueueImprovement: 41.5,
  maxQueueImprovement: 16.2,
  meanSpeedImprovement: 15.6,
  throughputImprovement: 36.9,
} as const

export type CityPulseQwenBenchmarkMetric = {
  key: 'avgQueue' | 'maxQueue' | 'meanSpeed' | 'throughput'
  label: string
  direction: 'up' | 'down'
  value: number
}

export const CITYPULSE_QWEN_BENCHMARK_METRICS: readonly CityPulseQwenBenchmarkMetric[] = [
  {
    key: 'avgQueue',
    label: '平均排队改善',
    direction: 'down',
    value: CITYPULSE_QWEN_BENCHMARK.avgQueueImprovement,
  },
  {
    key: 'maxQueue',
    label: '最大排队改善',
    direction: 'down',
    value: CITYPULSE_QWEN_BENCHMARK.maxQueueImprovement,
  },
  {
    key: 'meanSpeed',
    label: '平均车速提升',
    direction: 'up',
    value: CITYPULSE_QWEN_BENCHMARK.meanSpeedImprovement,
  },
  {
    key: 'throughput',
    label: '吞吐流率提升',
    direction: 'up',
    value: CITYPULSE_QWEN_BENCHMARK.throughputImprovement,
  },
]
