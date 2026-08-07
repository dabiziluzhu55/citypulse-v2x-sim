import type { SimulationSnapshot } from '../types/simulation'

export function simulationSnapshotErrorMessage(
  snapshot: Pick<SimulationSnapshot, 'state' | 'error'>,
): string | null {
  const detail = snapshot.error?.trim()
  if (!detail && snapshot.state !== 'FAILED') return null
  if (!detail) return '仿真运行失败，后端未提供错误详情'
  if (/fingerprint|topology|拓扑/i.test(detail)) {
    return `算法模型与当前路网拓扑不匹配。后端详情：${detail}`
  }
  if (/tensor|shape|dimension|action.?mask|张量|动作掩码/i.test(detail)) {
    return `算法模型输入或动作契约不兼容。后端详情：${detail}`
  }
  return `仿真运行失败。后端详情：${detail}`
}
