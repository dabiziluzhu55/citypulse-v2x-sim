import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { XMLParser } from 'fast-xml-parser'
import { projectBd09ToWebMercator, wgs84ToBd09 } from '../src/mapv/sceneCoordinates.ts'
import { rebuildRoadEdgeGeometry } from '../src/mapv/realistic/intersectionRoadGeometry.ts'
import {
  REBUILD_RADIUS_METERS,
  cropPolylineToRadius,
  parseShape,
  toLocalShape,
  validateIntersectionManifest,
} from './realistic-intersection-core.mjs'

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendDirectory = path.resolve(scriptsDirectory, '..')
const dataDirectory = path.resolve(frontendDirectory, '../data/maps/sumo')
const sourcePath = path.resolve(dataDirectory, 'generated/network/TotalMap_20.signals.net.xml')
const mappingPath = path.resolve(dataDirectory, 'TotalMap_20.intersections.json')
const tlsManifestPath = path.resolve(dataDirectory, 'generated/manifests/tls_manifest.json')
const converterPath = path.resolve(scriptsDirectory, 'convert-sumo-coordinates.py')
const outputDirectory = path.resolve(frontendDirectory, 'public/intersections/v3')
const requestedIntersectionId = process.argv
  .find((argument) => argument.startsWith('--intersection='))
  ?.split('=', 2)[1]

function asArray(value) {
  if (value === undefined) return []
  return Array.isArray(value) ? value : [value]
}

function roundShape(shape) {
  return shape.map(([x, y]) => [Number(x.toFixed(3)), Number(y.toFixed(3))])
}

function numericDemoOrder(left, right) {
  return Number(left.replace('demo_', '')) - Number(right.replace('demo_', ''))
}

function laneKind(lane) {
  const allow = String(lane.allow ?? '').split(/\s+/)
  const type = String(lane.type ?? '').toLowerCase()
  if (type.includes('sidewalk') || allow.includes('pedestrian')) return 'pedestrian'
  if (allow.includes('bicycle') && !allow.some((value) => ['passenger', 'private', 'bus', 'truck'].includes(value))) {
    return 'bicycle'
  }
  return 'driving'
}

function convertSumoCoordinates(points) {
  const pythonCommands = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']
  const failures = []
  for (const command of pythonCommands) {
    const result = spawnSync(command, [converterPath, sourcePath], {
      cwd: frontendDirectory,
      encoding: 'utf8',
      input: JSON.stringify(points),
      maxBuffer: 32 * 1024 * 1024,
    })
    if (result.status === 0) return JSON.parse(result.stdout)
    failures.push(`${command}: ${result.stderr?.trim() || result.error?.message || `exit ${result.status}`}`)
  }
  throw new Error(`SUMO coordinate conversion failed:\n${failures.join('\n')}`)
}

function toWebMercator([longitude, latitude]) {
  return projectBd09ToWebMercator(wgs84ToBd09(longitude, latitude))
}

const [source, mappingSource, tlsManifestSource] = await Promise.all([
  readFile(sourcePath, 'utf8'),
  readFile(mappingPath, 'utf8'),
  readFile(tlsManifestPath, 'utf8'),
])
const mapping = JSON.parse(mappingSource)
const tlsManifest = JSON.parse(tlsManifestSource)
const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: true,
})
const net = parser.parse(source).net
const junctions = new Map(asArray(net.junction).map((junction) => [String(junction.id), junction]))
const edges = asArray(net.edge).filter((edge) => !edge.function)
const tlLogics = new Map(asArray(net.tlLogic).map((logic) => [String(logic.id), logic]))
const sourceSha256 = createHash('sha256').update(source).digest('hex')
const pending = []

const availableIntersectionIds = Object.keys(mapping)
  .filter((intersectionId) => tlsManifest.intersections?.[intersectionId])
  .filter((intersectionId) => !requestedIntersectionId || intersectionId === requestedIntersectionId)
  .sort(numericDemoOrder)
if (requestedIntersectionId && !availableIntersectionIds.includes(requestedIntersectionId)) {
  throw new Error(`TLS manifest is missing ${requestedIntersectionId}`)
}

for (const intersectionId of availableIntersectionIds) {
  const location = mapping[intersectionId]
  const topology = tlsManifest.intersections?.[intersectionId]
  const junctionId = String(location.junction_id)
  const junction = junctions.get(junctionId)
  if (!junction) throw new Error(`SUMO network is missing junction ${junctionId}`)
  const sumoOrigin = [Number(junction.x), Number(junction.y)]
  const incidentEdges = edges.filter((edge) => (
    String(edge.from) === junctionId || String(edge.to) === junctionId
  ))
  const renderedEdges = incidentEdges.map((edge) => ({
    id: String(edge.id),
    incoming: String(edge.to) === junctionId,
    lanes: asArray(edge.lane).map((lane) => ({
      id: String(lane.id),
      index: Number(lane.index),
      kind: laneKind(lane),
      width: Number(lane.width) || 3.2,
      speed: Number(lane.speed) || 13.9,
      points: roundShape(toLocalShape(parseShape(String(lane.shape)), sumoOrigin)),
    })).filter((lane) => lane.points.length >= 2).sort((a, b) => a.index - b.index),
  })).filter((edge) => edge.lanes.length > 0).sort((a, b) => a.id.localeCompare(b.id))

  const connections = asArray(topology.connections).map((connection) => ({
    tlsId: String(connection.tls_id),
    linkIndex: Number(connection.link_index),
    fromEdge: String(connection.from_edge),
    fromLane: Number(connection.from_lane),
    toEdge: String(connection.to_edge),
    toLane: Number(connection.to_lane),
    direction: ['s', 'l', 'r', 't'].includes(String(connection.direction).toLowerCase())
      ? String(connection.direction).toLowerCase()
      : 's',
    directionLabel: ({ s: 'through', l: 'left', r: 'right', t: 'u-turn' })[
      String(connection.direction).toLowerCase()
    ] ?? String(connection.direction),
  })).sort((a, b) => a.tlsId.localeCompare(b.tlsId) || a.linkIndex - b.linkIndex)

  const signalGroupMap = new Map()
  for (const connection of connections) {
    const key = `${connection.tlsId}:${connection.fromEdge}:${connection.fromLane}`
    const current = signalGroupMap.get(key) ?? {
      tlsId: connection.tlsId,
      laneId: `${connection.fromEdge}_${connection.fromLane}`,
      linkIndexes: [],
    }
    current.linkIndexes.push(connection.linkIndex)
    signalGroupMap.set(key, current)
  }

  const phases = [...tlLogics.entries()]
    .filter(([tlsId]) => asArray(topology.tls_ids).map(String).includes(tlsId))
    .flatMap(([tlsId, logic]) => asArray(logic.phase).map((phase, index) => ({
      tlsId,
      index,
      durationSeconds: Number(phase.duration),
      state: String(phase.state),
      label: `SUMO phase ${index + 1}`,
    })))

  pending.push({
    sumoOrigin,
    requestedOrigin: [Number(location.lon), Number(location.lat)],
    manifest: {
      schemaVersion: 3,
      intersectionId,
      junctionId,
      tlsIds: asArray(topology.tls_ids).map(String),
      sourceCoordinateSystem: 'SUMO XY, tmerc',
      renderCoordinateSystem: 'LOCAL_BD09_WEB_MERCATOR_METERS, Z-up',
      sourceSha256,
      origin: {
        x: sumoOrigin[0],
        y: sumoOrigin[1],
        longitude: 0,
        latitude: 0,
        bd09: [0, 0],
        webMercator: [0, 0],
      },
      requestedOrigin: { longitude: Number(location.lon), latitude: Number(location.lat) },
      horizontalScale: 1,
      sumoUnitScale: 1,
      radiusMeters: REBUILD_RADIUS_METERS,
      radiusSceneUnits: REBUILD_RADIUS_METERS,
      junctionShape: roundShape(toLocalShape(parseShape(String(junction.shape)), sumoOrigin)),
      edges: renderedEdges,
      connections,
      phases,
      phaseTemplates: topology.templates ?? {},
      signalGroups: [...signalGroupMap.values()].map((group) => ({
        ...group,
        linkIndexes: [...new Set(group.linkIndexes)].sort((a, b) => a - b),
      })),
    },
  })
}

const conversionPoints = []
const registrations = []
function registerPoint(point, apply) {
  registrations.push({ index: conversionPoints.length, apply })
  conversionPoints.push(point)
}

for (const item of pending) {
  const { manifest, sumoOrigin } = item
  registerPoint(sumoOrigin, (value) => { item.originWgs84 = value })
  registerPoint([sumoOrigin[0] + 1, sumoOrigin[1]], (value) => { item.axisXWgs84 = value })
  registerPoint([sumoOrigin[0], sumoOrigin[1] + 1], (value) => { item.axisYWgs84 = value })
  manifest.junctionShape.forEach((point, index) => registerPoint(
    [sumoOrigin[0] + point[0], sumoOrigin[1] + point[1]],
    (value) => { item.junctionWgs84 ??= []; item.junctionWgs84[index] = value },
  ))
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      lane.points.forEach((point, index) => registerPoint(
        [sumoOrigin[0] + point[0], sumoOrigin[1] + point[1]],
        (value) => { lane.wgs84 ??= []; lane.wgs84[index] = value },
      ))
    }
  }
}

const converted = convertSumoCoordinates(conversionPoints)
registrations.forEach(({ index, apply }) => apply(converted[index]))

await mkdir(outputDirectory, { recursive: true })
const existingCatalog = await readFile(path.resolve(outputDirectory, 'catalog.json'), 'utf8')
  .then(JSON.parse)
  .catch(() => ({ intersections: [] }))
const catalog = {
  schemaVersion: 3,
  generatedAt: new Date().toISOString(),
  sourceSha256,
  intersections: existingCatalog.intersections.filter(
    (entry) => !availableIntersectionIds.includes(entry.intersectionId),
  ),
}

for (const item of pending) {
  const { manifest } = item
  const originWgs84 = item.originWgs84
  const originBd09 = wgs84ToBd09(...originWgs84)
  const originPlane = projectBd09ToWebMercator(originBd09)
  const axisXPlane = toWebMercator(item.axisXWgs84)
  const axisYPlane = toWebMercator(item.axisYWgs84)
  const scaleX = Math.hypot(axisXPlane[0] - originPlane[0], axisXPlane[1] - originPlane[1])
  const scaleY = Math.hypot(axisYPlane[0] - originPlane[0], axisYPlane[1] - originPlane[1])
  const sumoUnitScale = (scaleX + scaleY) / 2
  const horizontalScale = 1 / Math.cos(originWgs84[1] * Math.PI / 180)

  manifest.origin.longitude = originWgs84[0]
  manifest.origin.latitude = originWgs84[1]
  manifest.origin.bd09 = originBd09
  manifest.origin.webMercator = originPlane
  manifest.horizontalScale = horizontalScale
  manifest.sumoUnitScale = sumoUnitScale
  manifest.radiusSceneUnits = manifest.radiusMeters * horizontalScale
  manifest.junctionShape = roundShape(item.junctionWgs84.map((point) => {
    const projected = toWebMercator(point)
    return [projected[0] - originPlane[0], projected[1] - originPlane[1]]
  }))
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      lane.widthMeters = lane.width
      lane.width = Number((lane.width * horizontalScale).toFixed(3))
      const projectedPoints = lane.wgs84.map((point) => {
        const projected = toWebMercator(point)
        return [projected[0] - originPlane[0], projected[1] - originPlane[1]]
      })
      lane.points = roundShape(cropPolylineToRadius(projectedPoints, manifest.radiusSceneUnits))
      delete lane.wgs84
    }
    const rebuilt = rebuildRoadEdgeGeometry(edge.lanes)
    edge.centerline = roundShape(rebuilt.centerline)
    edge.roadWidth = Number(rebuilt.roadWidth.toFixed(3))
    edge.lanes.forEach((lane, index) => {
      lane.renderPoints = roundShape(rebuilt.renderPoints[index])
    })
  }

  const errors = validateIntersectionManifest(manifest)
  if (errors.length > 0) {
    throw new Error(`${manifest.intersectionId} manifest is invalid:\n- ${errors.join('\n- ')}`)
  }
  const intersectionDirectory = path.resolve(outputDirectory, manifest.intersectionId)
  await mkdir(intersectionDirectory, { recursive: true })
  await writeFile(
    path.resolve(intersectionDirectory, 'manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`,
    'utf8',
  )
  catalog.intersections.push({
    intersectionId: manifest.intersectionId,
    junctionId: manifest.junctionId,
    tlsIds: manifest.tlsIds,
    longitude: manifest.origin.longitude,
    latitude: manifest.origin.latitude,
    horizontalScale: manifest.horizontalScale,
    radiusMeters: manifest.radiusMeters,
    assetUrl: `/intersections/v3/${manifest.intersectionId}/manifest.json`,
  })
}

catalog.intersections.sort((left, right) => numericDemoOrder(left.intersectionId, right.intersectionId))

await writeFile(
  path.resolve(outputDirectory, 'catalog.json'),
  `${JSON.stringify(catalog, null, 2)}\n`,
  'utf8',
)
console.log(`Updated ${pending.length} projection-correct intersections; catalog contains ${catalog.intersections.length}`)
