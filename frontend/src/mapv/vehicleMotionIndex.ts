export interface VehicleMotionLaneIndexEntry {
  laneId: string
  edgeId: string
  laneIndex: number
  intersectionId: string
  internal: boolean
  widthMeters: number
  lengthMeters: number
  sourcePoints: Array<[number, number]>
  coordinates: Array<[number, number]>
}

export interface VehicleMotionConnectionIndexEntry {
  connectionId: string
  fromLaneId: string
  toLaneId: string
  viaLaneIds: string[]
  direction: string
}

export interface VehicleMotionIndexGeneration {
  schemaVersion: 1
  networkSource: { path: string; sha256: string }
  intersectionCatalogSha256: string
  coordinateSystems: {
    source: 'SUMO_XY_METERS'
    geographic: 'WGS84'
  }
  laneCount: number
  connectionCount: number
  lanes: VehicleMotionLaneIndexEntry[]
  connections: VehicleMotionConnectionIndexEntry[]
}

let indexPromise: Promise<VehicleMotionIndexGeneration> | null = null

function finitePoint(value: unknown): value is [number, number] {
  return Array.isArray(value)
    && value.length >= 2
    && Number.isFinite(Number(value[0]))
    && Number.isFinite(Number(value[1]))
}

export function parseVehicleMotionIndex(value: unknown): VehicleMotionIndexGeneration {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Vehicle motion index must be an object')
  }
  const source = value as Partial<VehicleMotionIndexGeneration>
  if (
    source.schemaVersion !== 1
    || !source.networkSource
    || !/^[a-f0-9]{64}$/.test(source.networkSource.sha256)
    || source.coordinateSystems?.source !== 'SUMO_XY_METERS'
    || source.coordinateSystems?.geographic !== 'WGS84'
    || !Array.isArray(source.lanes)
    || !Array.isArray(source.connections)
    || source.laneCount !== source.lanes.length
    || source.connectionCount !== source.connections.length
  ) throw new Error('Vehicle motion index is incompatible')

  const laneIds = new Set<string>()
  for (const lane of source.lanes) {
    if (
      !lane
      || !lane.laneId
      || laneIds.has(lane.laneId)
      || !lane.edgeId
      || !Number.isInteger(lane.laneIndex)
      || !Number.isFinite(lane.widthMeters)
      || lane.widthMeters <= 0
      || !Array.isArray(lane.sourcePoints)
      || lane.sourcePoints.length < 2
      || !lane.sourcePoints.every(finitePoint)
      || !Array.isArray(lane.coordinates)
      || lane.coordinates.length !== lane.sourcePoints.length
      || !lane.coordinates.every(finitePoint)
    ) throw new Error(`Vehicle motion index contains an invalid lane ${lane?.laneId ?? ''}`)
    laneIds.add(lane.laneId)
  }
  const connectionIds = new Set<string>()
  for (const connection of source.connections) {
    if (
      !connection
      || !connection.connectionId
      || connectionIds.has(connection.connectionId)
      || !laneIds.has(connection.fromLaneId)
      || !laneIds.has(connection.toLaneId)
      || !Array.isArray(connection.viaLaneIds)
      || !connection.viaLaneIds.every((laneId) => laneIds.has(laneId))
    ) throw new Error(`Vehicle motion index contains an invalid connection ${connection?.connectionId ?? ''}`)
    connectionIds.add(connection.connectionId)
  }
  return source as VehicleMotionIndexGeneration
}

export async function loadVehicleMotionIndex(
  url = '/intersections/v3/vehicle-motion-index.json',
): Promise<VehicleMotionIndexGeneration> {
  indexPromise ??= fetch(url).then(async (response) => {
    if (!response.ok) throw new Error(`Vehicle motion index returned HTTP ${response.status}`)
    return parseVehicleMotionIndex(await response.json())
  })
  return indexPromise
}
