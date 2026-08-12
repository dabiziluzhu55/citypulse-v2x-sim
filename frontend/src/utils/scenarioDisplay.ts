import { formatIntersectionLabel } from './intersectionLabels.ts'

const SCENARIO_LABELS: Record<string, string> = {
  xiongan_20: '雄安20路口路网',
  east_dense: '东部密集区场景',
  west_dense: '西部密集区场景',
}

const PERIOD_LABELS: Record<string, string> = {
  morning_peak: '早高峰',
  off_peak: '平峰',
  evening_peak: '晚高峰',
}

const PERIOD_START_SECONDS: Record<string, number> = {
  morning_peak: 7 * 3600,
  off_peak: 14 * 3600 + 30 * 60,
  evening_peak: 17 * 3600 + 30 * 60,
}

const ORIGIN_LABELS: Record<string, string> = {
  east: '东进口',
  south: '南进口',
  west: '西进口',
  north: '北进口',
}

export function formatScenarioPresetLabel(value: string): string {
  return SCENARIO_LABELS[value] ?? value
}

export function formatSimulationPeriodLabel(value: string): string {
  return PERIOD_LABELS[value] ?? value
}

export function formatIntersectionLabels(values: readonly string[]): string {
  return [...new Set(values)]
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))
    .map(formatIntersectionLabel)
    .join('、') || '无'
}

export function formatSimulationClock(totalSeconds: number): string {
  const normalized = Math.max(0, Math.round(totalSeconds))
  const hours = Math.floor(normalized / 3600) % 24
  const minutes = Math.floor((normalized % 3600) / 60)
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`
}

export function formatSimulationWindow(
  period: string,
  windowStartSeconds: number,
  durationSeconds: number,
): string {
  const absoluteStart = (PERIOD_START_SECONDS[period] ?? 0) + windowStartSeconds
  return `${formatSimulationClock(absoluteStart)}-${formatSimulationClock(absoluteStart + durationSeconds)}`
}

export function formatSimulationOrigins(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return JSON.stringify(value)
  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length === 0) return '无'
  return entries
    .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
    .map(([intersectionId, origins]) => {
      const labels = Array.isArray(origins)
        ? origins.map((origin) => ORIGIN_LABELS[String(origin)] ?? String(origin)).join('、')
        : String(origins)
      return `${formatIntersectionLabel(intersectionId)}：${labels || '无'}`
    })
    .join('；')
}
