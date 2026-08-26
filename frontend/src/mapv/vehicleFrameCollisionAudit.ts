import type { VehicleTwinSample } from './vehicleTwinSample'

export interface VehicleFrameCollisionAudit {
  acceptedSamples: VehicleTwinSample[]
  sourceIntersectionCount: number
  visualAddedIntersectionCount: number
  rejectedVehicleIds: string[]
}

interface OrientedBox {
  centerX: number
  centerY: number
  forwardX: number
  forwardY: number
  halfLength: number
  halfWidth: number
}

const GRID_METERS = 18
const INTERSECTION_EPSILON_METERS = 0.03

function meters(longitude: number, latitude: number): [number, number] {
  const latitudeRadians = latitude * Math.PI / 180
  return [longitude * 111_320 * Math.cos(latitudeRadians), latitude * 110_574]
}

function boxForSample(sample: VehicleTwinSample, source: boolean): OrientedBox | null {
  const longitude = source
    ? Number(sample.authoritativeSourceLongitude)
    : Number(sample.point[0])
  const latitude = source
    ? Number(sample.authoritativeSourceLatitude)
    : Number(sample.point[1])
  const length = Number(sample.vehicleLengthMeters)
  const width = Number(sample.vehicleWidthMeters)
  const heading = Number(sample.vehicleHeading)
  if (
    !Number.isFinite(longitude)
    || !Number.isFinite(latitude)
    || !Number.isFinite(length)
    || !Number.isFinite(width)
    || !Number.isFinite(heading)
    || length <= 0
    || width <= 0
  ) return null
  const [x, y] = meters(longitude, latitude)
  const forwardX = Math.cos(heading)
  const forwardY = Math.sin(heading)
  return {
    centerX: source ? x - forwardX * length / 2 : x,
    centerY: source ? y - forwardY * length / 2 : y,
    forwardX,
    forwardY,
    halfLength: length / 2,
    halfWidth: width / 2,
  }
}

function projectionRadius(box: OrientedBox, axisX: number, axisY: number): number {
  const lateralX = -box.forwardY
  const lateralY = box.forwardX
  return box.halfLength * Math.abs(box.forwardX * axisX + box.forwardY * axisY)
    + box.halfWidth * Math.abs(lateralX * axisX + lateralY * axisY)
}

function boxesIntersect(left: OrientedBox, right: OrientedBox): boolean {
  const axes = [
    [left.forwardX, left.forwardY],
    [-left.forwardY, left.forwardX],
    [right.forwardX, right.forwardY],
    [-right.forwardY, right.forwardX],
  ]
  const centerDeltaX = right.centerX - left.centerX
  const centerDeltaY = right.centerY - left.centerY
  return axes.every(([axisX, axisY]) => {
    const separation = Math.abs(centerDeltaX * axisX + centerDeltaY * axisY)
    return projectionRadius(left, axisX, axisY)
      + projectionRadius(right, axisX, axisY)
      - separation > INTERSECTION_EPSILON_METERS
  })
}

export function auditVehicleFrameCollisions(
  samples: readonly VehicleTwinSample[],
): VehicleFrameCollisionAudit {
  const visualBoxes = new Map<string, OrientedBox>()
  const sourceBoxes = new Map<string, OrientedBox>()
  const buckets = new Map<string, string[]>()
  for (const sample of samples) {
    const visual = boxForSample(sample, false)
    const source = boxForSample(sample, true)
    if (!visual || !source) continue
    visualBoxes.set(sample.id, visual)
    sourceBoxes.set(sample.id, source)
    const column = Math.floor(visual.centerX / GRID_METERS)
    const row = Math.floor(visual.centerY / GRID_METERS)
    const key = `${column}:${row}`
    buckets.set(key, [...(buckets.get(key) ?? []), sample.id])
  }

  const checkedPairs = new Set<string>()
  const rejected = new Set<string>()
  let sourceIntersectionCount = 0
  let visualAddedIntersectionCount = 0
  for (const [key, ids] of buckets) {
    const [column, row] = key.split(':').map(Number)
    const neighbors: string[] = []
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        neighbors.push(...(buckets.get(`${column + dx}:${row + dy}`) ?? []))
      }
    }
    for (const leftId of ids) {
      for (const rightId of neighbors) {
        if (leftId === rightId) continue
        const pairKey = [leftId, rightId].sort().join('|')
        if (checkedPairs.has(pairKey)) continue
        checkedPairs.add(pairKey)
        const leftVisual = visualBoxes.get(leftId)
        const rightVisual = visualBoxes.get(rightId)
        const leftSource = sourceBoxes.get(leftId)
        const rightSource = sourceBoxes.get(rightId)
        if (!leftVisual || !rightVisual || !leftSource || !rightSource) continue
        const visualIntersects = boxesIntersect(leftVisual, rightVisual)
        if (!visualIntersects) continue
        if (boxesIntersect(leftSource, rightSource)) {
          sourceIntersectionCount += 1
          continue
        }
        visualAddedIntersectionCount += 1
        rejected.add(leftId.localeCompare(rightId) > 0 ? leftId : rightId)
      }
    }
  }
  return {
    acceptedSamples: samples.filter((sample) => !rejected.has(sample.id)),
    sourceIntersectionCount,
    visualAddedIntersectionCount,
    rejectedVehicleIds: [...rejected].sort(),
  }
}
