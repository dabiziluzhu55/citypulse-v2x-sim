import type { CollaborationLogEntry } from '../types/collaboration'

export type CommunicationFlow = 'vehicle_cloud' | 'road_cloud' | 'cloud_road' | 'cloud_vehicle'

export const COMMUNICATION_FLOW_PARTS: Record<CommunicationFlow, [string, string]> = {
  vehicle_cloud: ['车', '云'],
  road_cloud: ['路', '云'],
  cloud_road: ['云', '路'],
  cloud_vehicle: ['云', '车'],
}

export function resolveCommunicationFlow(entry: CollaborationLogEntry): CommunicationFlow {
  const source = entry.source.toLowerCase()
  const message = entry.message

  if (source === 'cloud') {
    return message.includes('建议') || message.includes('下发') ? 'cloud_vehicle' : 'cloud_road'
  }

  if (source === 'sumo') {
    return 'cloud_road'
  }

  if (message.includes('反馈') || /^v[\d_]/i.test(entry.source) || source.includes('vehicle')) {
    return 'vehicle_cloud'
  }

  if (message.includes('建议速度') || message.includes('建议路径')) {
    return 'cloud_vehicle'
  }

  return 'road_cloud'
}

export function formatCommunicationFlowParts(entry: CollaborationLogEntry): [string, string] {
  return COMMUNICATION_FLOW_PARTS[resolveCommunicationFlow(entry)]
}

/** @deprecated Use formatCommunicationFlowParts */
export function formatCommunicationFlow(entry: CollaborationLogEntry): string {
  const [from, to] = formatCommunicationFlowParts(entry)
  return `${from} ▶ ${to}`
}

export function formatLogClock(timeLabel: string): string {
  if (/^\d{2}:\d{2}:\d{2}$/.test(timeLabel)) {
    const [h, m, s] = timeLabel.split(':')
    return `${h} : ${m} : ${s}`
  }

  if (/^\d{2}:\d{2}$/.test(timeLabel)) {
    const [a, b] = timeLabel.split(':')
    return `${a} : ${b}`
  }

  return timeLabel
}
