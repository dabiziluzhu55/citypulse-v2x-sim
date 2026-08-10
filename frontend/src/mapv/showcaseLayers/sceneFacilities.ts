export type SignalColor = 'red' | 'yellow' | 'green'
export type TurnMovement = 'left' | 'through' | 'right'

export interface SceneFacilityPoint {
  id: string
  position: [number, number, number]
  heading: number
}

export interface SceneSignal extends SceneFacilityPoint {
  approach: string
  tlsId: string
  linkIndices: number[]
}

export interface SceneArrow extends SceneFacilityPoint {
  approach: string
  laneIndex: number
  movements: TurnMovement[]
}

interface PhaseTemplate {
  green: string
  yellow: string
  clearance: string
}

export interface SceneFacilityManifest {
  schemaVersion: 2
  intersectionId: string
  sourceGeneratedAt: string
  lamps: SceneFacilityPoint[]
  cameras: SceneFacilityPoint[]
  signals: SceneSignal[]
  arrows: SceneArrow[]
  phaseTemplates: Record<string, Record<string, PhaseTemplate>>
}

interface Road {
  id: string
  coordinates: Array<[number, number]>
  local: XY[]
  width: number
  laneCount: number
}

interface Connection {
  approach: string
  fromEdge: string
  linkIndex: number
  movement: string
  fromLane: number
  tlsId: string
}

type XY = [number, number]

const METERS_PER_DEGREE = 110_900
const LAMP_SPACING_METERS = 70
const JUNCTION_CLEARANCE_METERS = 60
const APPROACH_LAMP_OFFSETS_METERS = [12, 42] as const
const MAX_LAMPS = 160
const MOVEMENT_ORDER: TurnMovement[] = ['left', 'through', 'right']

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`)
  }
  return value as Record<string, unknown>
}

function finiteCoordinate(value: unknown, label: string): [number, number] {
  if (!Array.isArray(value) || !Number.isFinite(value[0]) || !Number.isFinite(value[1])) {
    throw new Error(`${label} must contain finite longitude and latitude`)
  }
  return [Number(value[0]), Number(value[1])]
}

function toLocal(point: [number, number], origin: [number, number]): XY {
  return [
    (point[0] - origin[0]) * Math.cos(origin[1] * Math.PI / 180) * METERS_PER_DEGREE,
    (point[1] - origin[1]) * METERS_PER_DEGREE,
  ]
}

function toWgs84(point: XY, origin: [number, number]): [number, number, number] {
  return [
    origin[0] + point[0] / (Math.cos(origin[1] * Math.PI / 180) * METERS_PER_DEGREE),
    origin[1] + point[1] / METERS_PER_DEGREE,
    0,
  ]
}

function add(a: XY, b: XY): XY {
  return [a[0] + b[0], a[1] + b[1]]
}

function subtract(a: XY, b: XY): XY {
  return [a[0] - b[0], a[1] - b[1]]
}

function scale(point: XY, amount: number): XY {
  return [point[0] * amount, point[1] * amount]
}

function length(point: XY): number {
  return Math.hypot(point[0], point[1])
}

function normalize(point: XY): XY {
  const magnitude = length(point) || 1
  return [point[0] / magnitude, point[1] / magnitude]
}

function distance(a: XY, b: XY): number {
  return length(subtract(a, b))
}

function dot(a: XY, b: XY): number {
  return a[0] * b[0] + a[1] * b[1]
}

function headingFor(direction: XY): number {
  return Math.atan2(direction[1], direction[0]) - Math.PI / 2
}

function parseRoads(collectionValue: unknown, origin: [number, number]): Road[] {
  const collection = record(collectionValue, 'Road GeoJSON')
  if (collection.type !== 'FeatureCollection' || !Array.isArray(collection.features)) {
    throw new Error('Road GeoJSON must be a FeatureCollection')
  }
  return collection.features.flatMap((candidate, index) => {
    const feature = record(candidate, `Road feature ${index}`)
    const geometry = record(feature.geometry, `Road feature ${index} geometry`)
    if (geometry.type !== 'LineString') return []
    if (!Array.isArray(geometry.coordinates) || geometry.coordinates.length < 2) {
      throw new Error(`Road feature ${index} must contain a LineString`)
    }
    const properties = record(feature.properties ?? {}, `Road feature ${index} properties`)
    const coordinates = geometry.coordinates.map((value, coordinateIndex) => (
      finiteCoordinate(value, `Road feature ${index} coordinate ${coordinateIndex}`)
    ))
    const id = String(properties.edge_id ?? feature.id ?? '')
    if (!id) throw new Error(`Road feature ${index} is missing edge_id`)
    return [{
      id,
      coordinates,
      local: coordinates.map((point) => toLocal(point, origin)),
      width: Math.max(2.8, Number(properties.width_m) || 6.7),
      laneCount: Math.max(1, Math.round(Number(properties.lane_count) || 1)),
    }]
  }).sort((a, b) => a.id.localeCompare(b.id))
}

function parseConnections(intersection: Record<string, unknown>, roads: Map<string, Road>): Connection[] {
  if (!Array.isArray(intersection.connections)) throw new Error('TLS connections must be an array')
  return intersection.connections.map((candidate, index) => {
    const value = record(candidate, `TLS connection ${index}`)
    const fromEdge = String(value.from_edge ?? '')
    const road = roads.get(fromEdge)
    if (!road) throw new Error(`TLS connection references missing road ${fromEdge}`)
    const fromLane = Number(value.from_lane)
    if (!Number.isInteger(fromLane) || fromLane < 0 || fromLane >= road.laneCount) {
      throw new Error(`TLS connection ${index} has invalid from_lane ${value.from_lane}`)
    }
    return {
      approach: String(value.approach ?? fromEdge),
      fromEdge,
      linkIndex: Number(value.link_index),
      movement: String(value.movement ?? ''),
      fromLane,
      tlsId: String(value.tls_id ?? ''),
    }
  })
}

function approachFrame(road: Road): { near: XY; direction: XY; normal: XY } {
  const first = road.local[0]
  const last = road.local.at(-1)!
  const atStart = length(first) <= length(last)
  const near = atStart ? first : last
  const neighbor = atStart ? road.local[1] : road.local.at(-2)!
  const direction = normalize(subtract(near, neighbor))
  return { near, direction, normal: [-direction[1], direction[0]] }
}

function sampleRoad(road: Road): Array<{ point: XY; direction: XY }> {
  const segments = road.local.slice(0, -1).map((point, index) => ({
    start: point,
    direction: normalize(subtract(road.local[index + 1], point)),
    length: distance(point, road.local[index + 1]),
  }))
  const total = segments.reduce((sum, segment) => sum + segment.length, 0)
  const result: Array<{ point: XY; direction: XY }> = []
  for (let target = LAMP_SPACING_METERS / 2; target < total; target += LAMP_SPACING_METERS) {
    let consumed = 0
    for (const segment of segments) {
      if (consumed + segment.length >= target) {
        result.push({
          point: add(segment.start, scale(segment.direction, target - consumed)),
          direction: segment.direction,
        })
        break
      }
      consumed += segment.length
    }
  }
  return result
}

function corridorRoads(roads: Road[]): Road[] {
  const used = new Set<string>()
  const result: Road[] = []
  // ponytail: O(n^2) pairing is simpler and bounded by the small per-scene road manifest.
  for (const road of roads) {
    if (used.has(road.id)) continue
    result.push(road)
    used.add(road.id)
    const direction = normalize(subtract(road.local.at(-1)!, road.local[0]))
    const opposite = roads.find((candidate) => {
      if (used.has(candidate.id)) return false
      const candidateDirection = normalize(subtract(candidate.local.at(-1)!, candidate.local[0]))
      return dot(direction, candidateDirection) < -0.8
        && distance(road.local[0], candidate.local.at(-1)!) < 30
        && distance(road.local.at(-1)!, candidate.local[0]) < 30
    })
    if (opposite) used.add(opposite.id)
  }
  return result
}

function parseTemplates(intersection: Record<string, unknown>): SceneFacilityManifest['phaseTemplates'] {
  const source = record(intersection.templates ?? {}, 'TLS templates')
  return Object.fromEntries(Object.entries(source).map(([phase, tlsValue]) => {
    const tlsTemplates = record(tlsValue, `TLS phase ${phase}`)
    return [phase, Object.fromEntries(Object.entries(tlsTemplates).map(([tlsId, templateValue]) => {
      const template = record(templateValue, `TLS ${tlsId} phase ${phase}`)
      return [tlsId, {
        green: String(template.green ?? ''),
        yellow: String(template.yellow ?? ''),
        clearance: String(template.clearance ?? ''),
      }]
    }))]
  }))
}

function validateFacilityPoint(value: unknown, label: string): void {
  const point = record(value, label)
  if (!String(point.id ?? '')) throw new Error(`${label} is missing id`)
  if (!Array.isArray(point.position) || point.position.length < 2 || !point.position.every(Number.isFinite)) {
    throw new Error(`${label} position must contain finite coordinates`)
  }
  if (!Number.isFinite(point.heading)) throw new Error(`${label} heading must be finite`)
}

export function parseSceneFacilityManifest(value: unknown): SceneFacilityManifest {
  const manifest = record(value, 'Scene facility manifest')
  if (manifest.schemaVersion !== 2) throw new Error('Scene facility manifest requires schemaVersion 2')
  if (!String(manifest.intersectionId ?? '')) throw new Error('Scene facility manifest is missing intersectionId')
  for (const key of ['lamps', 'cameras', 'signals', 'arrows'] as const) {
    if (!Array.isArray(manifest[key])) throw new Error(`Scene facility manifest ${key} must be an array`)
    manifest[key].forEach((item, index) => validateFacilityPoint(item, `${key}[${index}]`))
  }
  for (const [index, candidate] of (manifest.signals as unknown[]).entries()) {
    const signal = record(candidate, `signals[${index}]`)
    if (!String(signal.tlsId ?? '') || !Array.isArray(signal.linkIndices)) {
      throw new Error(`signals[${index}] must contain tlsId and linkIndices`)
    }
    if (!signal.linkIndices.every((item) => Number.isInteger(item) && Number(item) >= 0)) {
      throw new Error(`signals[${index}] linkIndices must be non-negative integers`)
    }
  }
  for (const [index, candidate] of (manifest.arrows as unknown[]).entries()) {
    const arrow = record(candidate, `arrows[${index}]`)
    if (!String(arrow.approach ?? '') || !Number.isInteger(arrow.laneIndex)) {
      throw new Error(`arrows[${index}] must contain approach and laneIndex`)
    }
    if (!Array.isArray(arrow.movements)
      || arrow.movements.length === 0
      || !arrow.movements.every((movement) => MOVEMENT_ORDER.includes(String(movement) as TurnMovement))) {
      throw new Error(`arrows[${index}] has invalid movements`)
    }
  }
  record(manifest.phaseTemplates, 'Scene facility phaseTemplates')
  return manifest as unknown as SceneFacilityManifest
}

export function buildSceneFacilityManifest(
  roadGeoJson: unknown,
  tlsManifest: unknown,
  intersectionId: string,
): SceneFacilityManifest {
  const collection = record(roadGeoJson, 'Road GeoJSON')
  const metadata = record(collection.metadata, 'Road GeoJSON metadata')
  const centerValue = record(metadata.center, 'Road GeoJSON center')
  const origin: [number, number] = [Number(centerValue.longitude), Number(centerValue.latitude)]
  if (!origin.every(Number.isFinite)) throw new Error('Road GeoJSON center must be finite')

  const roads = parseRoads(collection, origin)
  const roadsById = new Map(roads.map((road) => [road.id, road]))
  const tlsRoot = record(tlsManifest, 'TLS manifest')
  const intersections = record(tlsRoot.intersections, 'TLS intersections')
  const intersection = record(intersections[intersectionId], `TLS intersection ${intersectionId}`)
  const connections = parseConnections(intersection, roadsById)

  const signalGroups = new Map<string, Connection[]>()
  for (const connection of connections) {
    const key = `${connection.tlsId}:${connection.approach}:${connection.fromEdge}`
    signalGroups.set(key, [...(signalGroups.get(key) ?? []), connection])
  }

  const signals: SceneSignal[] = []
  const cameras: SceneFacilityPoint[] = []
  const arrows: SceneArrow[] = []
  for (const [key, group] of [...signalGroups.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    const first = group[0]
    const road = roadsById.get(first.fromEdge)!
    const frame = approachFrame(road)
    const roadsideOffset = road.width / 2 + 1.5
    const signalPoint = add(add(frame.near, scale(frame.direction, -8)), scale(frame.normal, roadsideOffset))
    const heading = headingFor(frame.direction)
    signals.push({
      id: `signal:${key}`,
      approach: first.approach,
      tlsId: first.tlsId,
      linkIndices: [...new Set(group.map((item) => item.linkIndex))].sort((a, b) => a - b),
      position: toWgs84(signalPoint, origin),
      heading,
    })
    cameras.push({
      id: `camera:${key}`,
      position: toWgs84(add(signalPoint, scale(frame.direction, -5)), origin),
      heading,
    })

    const laneMovements = new Map<number, Set<TurnMovement>>()
    for (const connection of group) {
      if (!MOVEMENT_ORDER.includes(connection.movement as TurnMovement)) continue
      const movements = laneMovements.get(connection.fromLane) ?? new Set<TurnMovement>()
      movements.add(connection.movement as TurnMovement)
      laneMovements.set(connection.fromLane, movements)
    }
    for (const [laneIndex, movementSet] of [...laneMovements.entries()].sort(([a], [b]) => a - b)) {
      const movements = MOVEMENT_ORDER.filter((movement) => movementSet.has(movement))
      const laneWidth = road.width / road.laneCount
      const lateral = (laneIndex - (road.laneCount - 1) / 2) * laneWidth
      arrows.push({
        id: `arrow:${key}:${laneIndex}`,
        approach: first.approach,
        laneIndex,
        movements,
        position: toWgs84(add(
          add(frame.near, scale(frame.direction, -17)),
          scale(frame.normal, lateral),
        ), origin),
        heading,
      })
    }
  }

  const lamps: SceneFacilityPoint[] = []
  const localLampRoads = new Set<string>()
  for (const group of signalGroups.values()) {
    const road = roadsById.get(group[0].fromEdge)!
    if (localLampRoads.has(road.id)) continue
    localLampRoads.add(road.id)
    const frame = approachFrame(road)
    for (const offset of APPROACH_LAMP_OFFSETS_METERS) {
      const point = add(frame.near, scale(frame.direction, -offset))
      for (const side of [-1, 1] as const) {
        lamps.push({
          id: `lamp:approach:${road.id}:${offset}:${side}`,
          position: toWgs84(add(point, scale(frame.normal, side * (road.width / 2 + 1.8))), origin),
          heading: headingFor(frame.direction),
        })
      }
    }
  }
  for (const road of corridorRoads(roads)) {
    for (const [sampleIndex, sample] of sampleRoad(road).entries()) {
      if (length(sample.point) < JUNCTION_CLEARANCE_METERS) continue
      const normal: XY = [-sample.direction[1], sample.direction[0]]
      for (const side of [-1, 1] as const) {
        if (lamps.length >= MAX_LAMPS) break
        lamps.push({
          id: `lamp:${road.id}:${sampleIndex}:${side}`,
          position: toWgs84(add(sample.point, scale(normal, side * (road.width / 2 + 1.8))), origin),
          heading: headingFor(sample.direction),
        })
      }
    }
  }

  return {
    schemaVersion: 2,
    intersectionId,
    sourceGeneratedAt: String(metadata.generated_at ?? ''),
    lamps: lamps.sort((a, b) => a.id.localeCompare(b.id)),
    cameras: cameras.sort((a, b) => a.id.localeCompare(b.id)),
    signals: signals.sort((a, b) => a.id.localeCompare(b.id)),
    arrows: arrows.sort((a, b) => a.id.localeCompare(b.id)),
    phaseTemplates: parseTemplates(intersection),
  }
}

export function resolveSignalColor(
  manifest: SceneFacilityManifest,
  signal: SceneSignal,
  phase: number | null,
  stage: string | null,
): SignalColor {
  if (phase == null || !stage) return 'red'
  const template = manifest.phaseTemplates[String(phase)]?.[signal.tlsId]
  if (!template) return 'red'
  const normalizedStage = stage.toLowerCase() as keyof PhaseTemplate
  const state = template[normalizedStage]
  if (!state) return 'red'
  const colors = signal.linkIndices.map((index) => state[index] ?? 'r')
  if (normalizedStage === 'green' && colors.some((value) => value === 'g' || value === 'G')) return 'green'
  if (normalizedStage === 'yellow' && colors.some((value) => value === 'y' || value === 'Y')) return 'yellow'
  return 'red'
}
