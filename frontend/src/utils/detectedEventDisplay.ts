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

export function detectedEventTypeLabel(card: DetectedEventCard): string {
  if (card.display_label) return card.display_label
  const labels: Record<string, string> = {
    localized_blockage: '局部占道',
    spillback: '排队溢出',
    unknown_abnormal: '交通异常',
    capacity_drop: '通行能力下降',
  }
  return labels[card.traffic_state] ?? card.traffic_state
}

export function detectedEventFlowSummary(card: DetectedEventCard): string {
  return card.prediction_summary?.trim() || '该路口短时流量预测尚未就绪'
}
