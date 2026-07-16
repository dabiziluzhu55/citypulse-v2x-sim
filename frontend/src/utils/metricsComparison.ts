import { formatNumber } from './format'
import type {
  AlgorithmResultMetrics,
  MetricComparisonRow,
} from '../types/metrics'

const COMPARISON_METRICS: Array<{
  key: keyof Pick<
    AlgorithmResultMetrics,
    'avg_waiting_time' | 'avg_travel_time' | 'avg_queue_length' | 'throughput' | 'fuel_consumption'
  >
  label: string
  format: (value: number) => string
  higherIsBetter: boolean
  useRatioForImprovement: boolean
}> = [
  {
    key: 'avg_waiting_time',
    label: '平均等待时间',
    format: (value) => `${formatNumber(value)}s`,
    higherIsBetter: false,
    useRatioForImprovement: false,
  },
  {
    key: 'avg_travel_time',
    label: '平均行程时间',
    format: (value) => `${formatNumber(value, 0)}s`,
    higherIsBetter: false,
    useRatioForImprovement: false,
  },
  {
    key: 'avg_queue_length',
    label: '平均排队长度',
    format: (value) => formatNumber(value),
    higherIsBetter: false,
    useRatioForImprovement: false,
  },
  {
    key: 'throughput',
    label: '通行量',
    format: (value) => `${formatNumber(value, 0)}veh/h`,
    higherIsBetter: true,
    useRatioForImprovement: true,
  },
  {
    key: 'fuel_consumption',
    label: '燃油消耗',
    format: (value) => `${formatNumber(value, 0)}%`,
    higherIsBetter: false,
    useRatioForImprovement: false,
  },
]

function formatImprovementRate(
  rate: number,
  useRatio: boolean,
): string {
  if (useRatio) {
    return rate.toFixed(3)
  }

  const sign = rate > 0 ? '+' : ''
  return `${sign}${rate.toFixed(2)}%`
}

export function buildFixedTimeComparisonRows(
  results: AlgorithmResultMetrics[],
  currentAlgorithmId: string,
  baselineAlgorithmId = 'fixed_time',
): MetricComparisonRow[] {
  const baseline = results.find((item) => item.algorithm === baselineAlgorithmId)
  const current =
    results.find((item) => item.algorithm === currentAlgorithmId) ??
    results.find((item) => item.algorithm !== baselineAlgorithmId)

  if (!baseline || !current) {
    return []
  }

  return COMPARISON_METRICS.map((config) => {
    const baselineValue = baseline[config.key]
    const currentValue = current[config.key]
    let improvementRate: number | null = null
    let improved: boolean | null = null

    if (baselineValue !== 0) {
      if (config.useRatioForImprovement) {
        improvementRate = currentValue / baselineValue - 1
      } else {
        improvementRate = ((currentValue - baselineValue) / baselineValue) * 100
      }
      improved = config.higherIsBetter ? improvementRate > 0 : improvementRate < 0
    }

    return {
      key: config.key,
      label: config.label,
      baselineLabel: baselineAlgorithmId,
      currentLabel: current.algorithm,
      baselineValue,
      currentValue,
      baselineDisplay: config.format(baselineValue),
      currentDisplay: config.format(currentValue),
      improvementRate,
      improvementDisplay:
        improvementRate != null
          ? formatImprovementRate(improvementRate, config.useRatioForImprovement)
          : '--',
      improved,
    }
  })
}
