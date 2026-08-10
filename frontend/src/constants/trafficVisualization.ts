export const TRAFFIC_STATUS_COLORS = {
  free: '#20f6a4',
  slow: '#ffd05a',
  congested: '#ff4d6d',
  default: '#21e6ff',
} as const

export const VEHICLE_COLORS = {
  normal: '#21e6ff',
  warning: '#ffd05a',
  danger: '#ff4d6d',
} as const

export const MAP_VIEW_WIDTH = 1000
export const MAP_VIEW_HEIGHT = 680

export function resolveTrafficStatusColor(status: string | undefined): string {
  if (status === 'free' || status === 'slow' || status === 'congested') {
    return TRAFFIC_STATUS_COLORS[status]
  }
  return TRAFFIC_STATUS_COLORS.default
}

export function resolveVehicleColor(waitingTime: number, speed: number): string {
  if (waitingTime >= 45 || speed < 1) {
    return VEHICLE_COLORS.danger
  }
  if (waitingTime >= 20 || speed < 4) {
    return VEHICLE_COLORS.warning
  }
  return VEHICLE_COLORS.normal
}
