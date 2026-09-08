import type {
  V2XLinkType,
  V2XLogStatus,
  V2XRole,
} from '../types/collaboration'
import type { V2XEventState } from '../types/simulation'
import { formatIntersectionLabel } from './intersectionLabels.ts'

export const COV2X_CONTROL_MODE = 'cov2x'

export const V2X_DIRECTION_FILTERS = [
  { value: 'all', label: '全部方向' },
  { value: 'vehicle->cloud', label: '车辆 → 云端' },
  { value: 'road->cloud', label: '路口 → 云端' },
  { value: 'cloud->road', label: '云端 → 路口' },
  { value: 'cloud->vehicle', label: '云端 → 车辆' },
  { value: 'road->vehicle', label: '路口 → 车辆' },
  { value: 'vehicle->road', label: '车辆 → 路口' },
] as const

export const V2X_LINK_FILTERS: readonly V2XLinkType[] = [
  'V2C',
  'I2C',
  'C2I',
  'C2V',
  'I2V',
  'V2I',
  'UNKNOWN',
]

const V2X_MESSAGE_META: Record<string, { tag: string; title: string }> = {
  VehicleStateV1: { tag: '车辆状态', title: '车辆向云端上报行驶状态' },
  IntersectionSummaryV1: { tag: '路口状态', title: '路口向云端上报排队情况' },
  RegionalPriorityV1: { tag: '协调指令', title: '云端下发协调优先级' },
  SPaTV2: { tag: '红绿灯配时', title: '路口向车辆发送红绿灯配时' },
  MAPV1: { tag: '路口地图', title: '路口向车辆发送道路地图' },
}

const LINK_TYPES: Record<string, V2XLinkType> = {
  'vehicle->road': 'V2I',
  'road->vehicle': 'I2V',
  'vehicle->cloud': 'V2C',
  'cloud->vehicle': 'C2V',
  'road->cloud': 'I2C',
  'cloud->road': 'C2I',
}

export function isCov2xControlMode(value: string | null | undefined): boolean {
  return String(value ?? '').trim() === COV2X_CONTROL_MODE
}

export function resolveV2XLinkType(
  source: V2XRole | string,
  destination: V2XRole | string,
): V2XLinkType {
  return LINK_TYPES[`${source}->${destination}`] ?? 'UNKNOWN'
}

export function resolveV2XStatus(event: V2XEventState | string): V2XLogStatus {
  if (event === 'TTL_EXPIRED') return 'failed'
  if (event === 'DELIVER' || event === 'CONSUME') return 'success'
  return 'sending'
}

export function formatV2XEndpoint(role: V2XRole | string, id: string): string {
  const raw = String(id ?? '').trim()
  if (role === 'cloud') {
    return '云端控制中心'
  }
  if (role === 'road') {
    return raw ? `RSU-${formatIntersectionLabel(raw)}` : 'RSU'
  }
  if (role === 'vehicle') {
    return '智能网联车'
  }
  return raw
}

export function resolveV2XMessageMeta(
  messageType: string,
  destinationRole?: V2XRole | string,
): { tag: string; title: string } {
  if (messageType === 'RegionalPriorityV1') {
    if (destinationRole === 'road') {
      return { tag: '路口协调', title: '云端向路口下发协调优先级' }
    }
    if (destinationRole === 'vehicle') {
      return { tag: '车辆协调', title: '云端向车辆下发协调优先级' }
    }
  }
  return V2X_MESSAGE_META[messageType] ?? {
    tag: messageType,
    title: messageType,
  }
}

function parseClockSeconds(value: string): number {
  const [hour = 0, minute = 0, second = 0] = value.split(':').map(Number)
  return hour * 3_600 + minute * 60 + second
}

export function formatCommunicationClock(
  eventSeconds: number,
  officialTime: string,
  elapsedSeconds: number,
): string {
  const startSeconds = parseClockSeconds(officialTime) - elapsedSeconds
  const normalized = ((startSeconds + eventSeconds) % 86_400 + 86_400) % 86_400
  const wholeSeconds = Math.floor(normalized)
  const hour = Math.floor(wholeSeconds / 3_600)
  const minute = Math.floor((wholeSeconds % 3_600) / 60)
  const second = wholeSeconds % 60
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`
}

export function v2xCommunicationEmptyText(controlMode: string | null | undefined): string {
  if (isCov2xControlMode(controlMode)) {
    return '等待车路云通信数据...'
  }
  return '当前管控算法未启用车路云协同通信'
}

export function latencyMsFromMessageAge(messageAgeSeconds: number | null | undefined): number {
  return Math.round((messageAgeSeconds ?? 0) * 1_000)
}

export function formatLatencyLabel(latencyMs: number | null | undefined): string {
  if (latencyMs == null || latencyMs <= 0) return '即时'
  return String(latencyMs)
}
