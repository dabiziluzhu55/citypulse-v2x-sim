export interface MapPoint {
  x: number
  y: number
}

export interface MapTransform {
  toScreen: (point: MapPoint) => MapPoint
  viewBox: string
}

export function createMapTransform(
  points: MapPoint[],
  width = 1000,
  height = 680,
  padding = 80,
): MapTransform {
  if (points.length === 0) {
    return {
      toScreen: (point) => point,
      viewBox: `0 0 ${width} ${height}`,
    }
  }

  const xs = points.map((point) => point.x)
  const ys = points.map((point) => point.y)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const spanX = Math.max(maxX - minX, 1)
  const spanY = Math.max(maxY - minY, 1)
  const scale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY)
  const offsetX = (width - spanX * scale) / 2
  const offsetY = (height - spanY * scale) / 2

  return {
    viewBox: `0 0 ${width} ${height}`,
    toScreen: (point) => ({
      x: offsetX + (point.x - minX) * scale,
      y: height - (offsetY + (point.y - minY) * scale),
    }),
  }
}
