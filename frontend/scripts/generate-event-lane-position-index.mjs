import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { XMLParser } from 'fast-xml-parser'

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendDirectory = path.resolve(scriptsDirectory, '..')
const projectDirectory = path.resolve(frontendDirectory, '..')
const networkPath = path.resolve(
  projectDirectory,
  'data/maps/sumo/generated/network/TotalMap_20.signals.net.xml',
)
const catalogPath = path.resolve(frontendDirectory, 'public/intersections/v3/catalog.json')
const converterPath = path.resolve(scriptsDirectory, 'convert-sumo-coordinates.py')
const outputPath = path.resolve(
  frontendDirectory,
  'public/intersections/v3/event-lane-position-index.json',
)

function asArray(value) {
  if (value === undefined || value === null) return []
  return Array.isArray(value) ? value : [value]
}

function parseShape(value) {
  if (typeof value !== 'string') return []
  return value.trim().split(/\s+/).flatMap((token) => {
    const [x, y] = token.split(',').map(Number)
    return Number.isFinite(x) && Number.isFinite(y) ? [[x, y]] : []
  })
}

function convertSumoCoordinates(points) {
  const commands = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']
  const failures = []
  for (const command of commands) {
    const result = spawnSync(command, [converterPath, networkPath], {
      cwd: frontendDirectory,
      encoding: 'utf8',
      input: JSON.stringify(points),
      maxBuffer: 256 * 1024 * 1024,
    })
    if (result.status === 0) return JSON.parse(result.stdout)
    failures.push(`${command}: ${result.stderr?.trim() || result.error?.message || `exit ${result.status}`}`)
  }
  throw new Error(`SUMO coordinate conversion failed:\n${failures.join('\n')}`)
}

function laneKind(lane) {
  const allow = String(lane.allow ?? '').split(/\s+/).filter(Boolean)
  if (allow.length && allow.every((value) => value === 'pedestrian')) return 'pedestrian'
  if (allow.length && allow.every((value) => ['bicycle', 'moped'].includes(value))) return 'bicycle'
  return 'driving'
}

function distanceMeters(left, right) {
  const latitude = (left[1] + right[1]) * Math.PI / 360
  const dx = (left[0] - right[0]) * 111_320 * Math.cos(latitude)
  const dy = (left[1] - right[1]) * 110_574
  return Math.hypot(dx, dy)
}

const [networkSource, catalogSource] = await Promise.all([
  readFile(networkPath, 'utf8'),
  readFile(catalogPath, 'utf8'),
])
const networkSha256 = createHash('sha256').update(networkSource).digest('hex')
const catalog = JSON.parse(catalogSource)
if (catalog.sourceSha256 !== networkSha256 || !Array.isArray(catalog.intersections)) {
  throw new Error('Intersection catalog and SUMO network generations do not match')
}

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: true,
})
const network = parser.parse(networkSource).net
const junctionOwners = new Map(catalog.intersections.flatMap((entry) => (
  [entry.junctionId, ...(entry.tlsIds ?? [])]
    .filter(Boolean)
    .map((junctionId) => [String(junctionId), entry.intersectionId])
)))
const lanes = asArray(network.edge).flatMap((edge) => asArray(edge.lane).flatMap((lane) => {
  const points = parseShape(lane.shape ?? edge.shape)
  if (points.length < 2 || typeof lane.id !== 'string') return []
  const directOwner = junctionOwners.get(String(edge.to ?? ''))
    ?? junctionOwners.get(String(edge.from ?? ''))
    ?? [...junctionOwners.entries()].find(([junctionId]) => String(edge.id).startsWith(`:${junctionId}_`))?.[1]
    ?? ''
  return [{
    laneId: lane.id,
    edgeId: String(edge.id),
    kind: laneKind(lane),
    directOwner,
    points,
  }]
}))

const uniquePoints = []
const indexesByKey = new Map()
const lanePointIndexes = lanes.map((lane) => lane.points.map(([x, y]) => {
  const key = `${x},${y}`
  const existing = indexesByKey.get(key)
  if (existing !== undefined) return existing
  const index = uniquePoints.length
  uniquePoints.push([x, y])
  indexesByKey.set(key, index)
  return index
}))
const converted = convertSumoCoordinates(uniquePoints)
const nodes = catalog.intersections.map((entry) => ({
  intersectionId: entry.intersectionId,
  coordinate: [entry.longitude, entry.latitude],
  radiusMeters: Number(entry.radiusMeters) || 520,
}))

const entries = lanes.flatMap((lane, laneIndex) => {
  const coordinates = lanePointIndexes[laneIndex].map((index) => {
    const [longitude, latitude] = converted[index]
    return [Number(longitude.toFixed(7)), Number(latitude.toFixed(7))]
  })
  const middle = coordinates[Math.floor(coordinates.length / 2)]
  const nearest = nodes
    .map((node) => ({ ...node, distance: distanceMeters(middle, node.coordinate) }))
    .sort((left, right) => left.distance - right.distance)[0]
  const intersectionId = lane.directOwner
    || (nearest && nearest.distance <= nearest.radiusMeters ? nearest.intersectionId : '')
  if (!intersectionId) return []
  return [{
    laneId: lane.laneId,
    edgeId: lane.edgeId,
    kind: lane.kind,
    intersectionId,
    coordinates,
  }]
}).sort((left, right) => left.laneId.localeCompare(right.laneId))

const payload = {
  schemaVersion: 1,
  generatedAt: new Date().toISOString(),
  networkSource: {
    path: 'data/maps/sumo/generated/network/TotalMap_20.signals.net.xml',
    sha256: networkSha256,
  },
  intersectionCatalogSha256: createHash('sha256').update(catalogSource).digest('hex'),
  laneCount: entries.length,
  entries,
}

await writeFile(outputPath, `${JSON.stringify(payload)}\n`, 'utf8')
console.log(JSON.stringify({ outputPath, laneCount: entries.length, networkSha256 }))
