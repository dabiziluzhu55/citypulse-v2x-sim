import type { AlgorithmType } from '../types/algorithm'

export const ALGORITHM_TYPE_LABELS: Record<string, string> = {
  baseline: '基线算法',
  rule_based: '规则算法',
  reinforcement_learning: '强化学习',
}

export function formatAlgorithmType(type: AlgorithmType): string {
  return ALGORITHM_TYPE_LABELS[type] ?? type
}
