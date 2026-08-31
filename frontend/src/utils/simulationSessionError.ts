import type { SimulationSnapshot } from '../types/simulation'

export function simulationSnapshotErrorMessage(
  snapshot: Pick<SimulationSnapshot, 'state' | 'error'>,
): string | null {
  const detail = snapshot.error?.trim()
  if (!detail && snapshot.state !== 'FAILED') return null
  if (!detail) return '仿真运行失败，后端未提供错误详情'
  if (/No module named ['"]?torch/i.test(detail)) {
    return `后端启动环境缺少 PyTorch，请使用项目 .venv 重启后端。后端详情：${detail}`
  }
  if (/fingerprint|topology|拓扑/i.test(detail)) {
    return `算法模型与当前路网拓扑不匹配。后端详情：${detail}`
  }
  const invalidRoute = detail.match(
    /Vehicle\s+['"]?[^'"]+['"]?\s+has no valid route\.\s+No connection between edge\s+['"]([^'"]+)['"]\s+and edge\s+['"]([^'"]+)['"]/i,
  )
  if (invalidRoute) {
    return `扰动车道导致路线不可达：道路 ${invalidRoute[1]} 无法连接到 ${invalidRoute[2]}。请删除或调整对应的施工占道事件后重新启动仿真。后端详情：${detail}`
  }
  if (/tensor|shape|dimension|action.?mask|张量|动作掩码/i.test(detail)) {
    return `算法模型输入或动作契约不兼容。后端详情：${detail}`
  }
  if (/model|checkpoint|weight|模型|权重/i.test(detail) && /not found|missing|不存在|缺失/i.test(detail)) {
    return `算法模型文件缺失，请检查本地模型路径。后端详情：${detail}`
  }
  return `仿真运行失败。后端详情：${detail}`
}
