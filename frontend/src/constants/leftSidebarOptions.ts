/** 左侧栏设计稿算法选项（文案与 Figma / SVG 一致） */
export interface LeftSidebarAlgorithmOption {
  value: string
  label: string
  applyId: string
}

export const LEFT_SIDEBAR_ALGORITHM_OPTIONS: LeftSidebarAlgorithmOption[] = [
  { value: 'fixed_time', label: '固定配时算法', applyId: 'fixed_time' },
  { value: 'max_pressure', label: 'Max Pressure算法', applyId: 'max_pressure' },
  { value: 'ippo_single', label: 'IPPO强化学习算法', applyId: 'ippo' },
  { value: 'ippo_multi', label: '多路口强化学习算法', applyId: 'ippo' },
]

export function resolveSidebarAlgorithmApplyId(value: string): string {
  return LEFT_SIDEBAR_ALGORITHM_OPTIONS.find((item) => item.value === value)?.applyId ?? value
}

export function toSidebarAlgorithmValue(algorithmId: string): string {
  if (algorithmId === 'ippo') {
    return 'ippo_multi'
  }
  const match = LEFT_SIDEBAR_ALGORITHM_OPTIONS.find((item) => item.applyId === algorithmId)
  return match?.value ?? algorithmId
}
