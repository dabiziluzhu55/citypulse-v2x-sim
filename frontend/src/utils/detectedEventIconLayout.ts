// 检测事件图标防重叠：固定网格分组 + 确定性小范围错位（2D/3D共用）

export interface ScreenMarkerInput {
  eventId: string
  x: number
  y: number
}

export interface ScreenMarkerLayout extends ScreenMarkerInput {
  offsetX: number
  offsetY: number
}

const DEFAULT_CELL_SIZE_PX = 36
const DEFAULT_RING_RADIUS_PX = 18

/** 环上确定性偏移，按 index 稳定排布 */
function ringOffset(index: number, count: number, radius: number): { offsetX: number; offsetY: number } {
  if (count <= 1) return { offsetX: 0, offsetY: 0 }
  const angle = (-Math.PI / 2) + (index * 2 * Math.PI) / count
  return {
    offsetX: Math.cos(angle) * radius,
    offsetY: Math.sin(angle) * radius,
  }
}

/**
 * 将屏幕坐标接近的图标分到同一网格单元，按 event_id 排序后环状错位。
 * 纯函数、无副作用；仅在事件列表/视角/缩放变化时由调用方触发。
 */
export function layoutDetectedEventIcons(
  markers: ScreenMarkerInput[],
  options?: {
    cellSizePx?: number
    ringRadiusPx?: number
  },
): ScreenMarkerLayout[] {
  if (markers.length === 0) return []
  const cellSize = options?.cellSizePx ?? DEFAULT_CELL_SIZE_PX
  const ringRadius = options?.ringRadiusPx ?? DEFAULT_RING_RADIUS_PX
  const groups = new Map<string, ScreenMarkerInput[]>()
  for (const marker of markers) {
    const cellX = Math.floor(marker.x / cellSize)
    const cellY = Math.floor(marker.y / cellSize)
    const key = `${cellX}:${cellY}`
    const bucket = groups.get(key)
    if (bucket) bucket.push(marker)
    else groups.set(key, [marker])
  }
  const laidOut: ScreenMarkerLayout[] = []
  for (const bucket of groups.values()) {
    bucket.sort((left, right) => left.eventId.localeCompare(right.eventId))
    bucket.forEach((marker, index) => {
      const offset = ringOffset(index, bucket.length, ringRadius)
      laidOut.push({
        eventId: marker.eventId,
        x: marker.x + offset.offsetX,
        y: marker.y + offset.offsetY,
        offsetX: offset.offsetX,
        offsetY: offset.offsetY,
      })
    })
  }
  return laidOut.sort((left, right) => left.eventId.localeCompare(right.eventId))
}

/** 视角/缩放变化指纹：用量化后的屏幕坐标避免每帧抖动重算 */
export function detectedEventLayoutKey(
  markers: ScreenMarkerInput[],
  viewToken: string | number = '',
  cellSizePx = DEFAULT_CELL_SIZE_PX,
): string {
  const parts = markers
    .map((marker) => {
      const cellX = Math.floor(marker.x / cellSizePx)
      const cellY = Math.floor(marker.y / cellSizePx)
      return `${marker.eventId}@${cellX},${cellY}`
    })
    .sort()
  return `${viewToken}|${parts.join(';')}`
}
