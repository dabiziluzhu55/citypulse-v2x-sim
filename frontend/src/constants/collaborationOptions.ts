export const STRATEGY_LABELS: Record<string, string> = {
  corridor_priority: '南北走廊优先',
  area_balance: '区域均衡',
  emergency_clearance: '应急疏散',
}

export const ALGORITHM_LABELS: Record<string, string> = {
  max_pressure: 'Max-Pressure',
  fixed_time: '固定配时',
  ippo: 'IPPO 强化学习',
  rule: '规则控制',
}

export const ACTION_LABELS: Record<string, string> = {
  extend_green: '延长绿灯',
  switch_phase: '切换相位',
  hold_phase: '保持相位',
}

export const ADVICE_LABELS: Record<string, string> = {
  speed_advice: '建议速度',
  path_advice: '建议路径',
}

export function formatStrategy(strategy: string): string {
  return STRATEGY_LABELS[strategy] ?? strategy
}

export function formatAlgorithm(algorithm: string): string {
  return ALGORITHM_LABELS[algorithm] ?? algorithm.toUpperCase()
}

export function formatActionType(actionType: string): string {
  return ACTION_LABELS[actionType] ?? actionType
}

export function formatAdviceType(type: string): string {
  return ADVICE_LABELS[type] ?? type
}

export function formatPhaseLabel(phase: number): string {
  const labels = ['南北直行', '东西直行', '左转专用', '全红']
  return labels[phase] ?? `相位 ${phase}`
}
