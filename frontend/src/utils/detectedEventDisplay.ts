// 算法识别事件展示文案与仿真时钟换算

import {
  formatOfficialTimeSeconds,
  parseOfficialTimeSeconds,
} from './confirmedSimulationClock'
import type { DetectedEventCard } from '../types/intelligence'
import type { SimulationSnapshot } from '../types/simulation'

export const DETECTED_EVENT_ICON_URL = '/icons/detected-event-warning.png'

export function activeDetectedEventCards(
  cards: DetectedEventCard[] | undefined | null,
): DetectedEventCard[] {
  if (!cards?.length) return []
  return cards.filter((card) => (
    card.status === 'active'
    && Number.isFinite(card.longitude)
    && Number.isFinite(card.latitude)
  ))
}

export function detectedEventClockTime(
  snapshot: Pick<SimulationSnapshot, 'official_time' | 'elapsed_seconds'> | null | undefined,
  eventStartSeconds: number,
): string {
  if (!snapshot) return formatOfficialTimeSeconds(eventStartSeconds)
  const current = parseOfficialTimeSeconds(snapshot.official_time)
  if (current == null) return formatOfficialTimeSeconds(eventStartSeconds)
  const origin = current - snapshot.elapsed_seconds
  return formatOfficialTimeSeconds(origin + eventStartSeconds)
}

/** 仿真时间口径：当前仿真秒 - 事件首次开始秒，不使用系统墙钟 */
export function detectedEventDurationSeconds(
  snapshot: Pick<SimulationSnapshot, 'elapsed_seconds'> | null | undefined,
  card: Pick<DetectedEventCard, 'start_seconds' | 'duration_seconds'>,
): number {
  if (snapshot && Number.isFinite(snapshot.elapsed_seconds)) {
    return Math.max(0, snapshot.elapsed_seconds - card.start_seconds)
  }
  return Math.max(0, Number(card.duration_seconds) || 0)
}

export function formatDetectedEventDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  const remain = total % 60
  return `${minutes} 分 ${remain} 秒`
}

export function detectedEventTypeLabel(card: DetectedEventCard): string {
  if (card.display_label) {
    // 兼容历史/后端旧文案
    if (card.display_label === '局部占道') return '疑似局部阻塞'
    return card.display_label
  }
  const labels: Record<string, string> = {
    localized_blockage: '疑似局部阻塞',
    spillback: '排队溢出',
    unknown_abnormal: '交通异常',
    capacity_drop: '通行能力下降',
  }
  return labels[card.traffic_state] ?? card.traffic_state
}

export function detectedEventFlowSummary(card: DetectedEventCard): string {
  return card.prediction_summary?.trim() || '该路口短时流量预测尚未就绪'
}
