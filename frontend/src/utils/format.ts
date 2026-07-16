import type { SimulationStatus } from '../types/overview'
import type { RunLifecycleStatus } from '../types/simulation'

export function formatSimTime(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const secs = totalSeconds % 60

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }

  return `${minutes}:${String(secs).padStart(2, '0')}`
}

export function formatNumber(value: number, fractionDigits = 1): string {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}

export function formatStatus(status: SimulationStatus): string {
  const labels: Record<SimulationStatus, string> = {
    idle: '空闲',
    running: '运行中',
    paused: '已暂停',
    stopped: '已停止',
    error: '异常',
  }
  return labels[status] ?? status
}

export function formatAlgorithm(name: string): string {
  return name.toUpperCase()
}

export function formatRunLifecycleStatus(status: RunLifecycleStatus): string {
  const labels: Record<RunLifecycleStatus, string> = {
    starting: '启动中',
    running: '运行中',
    paused: '已暂停',
    stopped: '已停止',
    idle: '空闲',
    error: '异常',
  }
  return labels[status] ?? status
}
