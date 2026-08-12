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
const converterPath = path.resolve(scriptsDirectory, 'convert-sumo-coordinates.py')
const outputPath = path.resolve(frontendDirectory, 'public/intersections/v3/full-road-network.geojson')

function asArray(value) {
  if (value === undefined) return []
  return Array.isArray(value) ? value : [value]
}

function parseShape(value) {
  if (typeof value !== 'string') return []
  return value.trim().split(/\s+/).flatMap((point) => {
    const [x, y] = point.split(',').map(Number)
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

const source = await readFile(networkPath, 'utf8')
const sourceSha256 = createHash('sha256').update(source).digest('hex')
const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: true,
})
const network = parser.parse(source).net
const roads = asArray(network.edge).flatMap((edge) => {
  if (edge.function) return []
  const lanes = asArray(edge.lane)
  const shape = parseShape(edge.shape)
  const fallbackShape = lanes
    .map((lane) => parseShape(lane.shape))
    .find((candidate) => candidate.length >= 2) ?? []
  const points = shape.length >= 2 ? shape : fallbackShape
  if (points.length < 2) return []
  return [{
    edgeId: String(edge.id),
    fromJunction: String(edge.from ?? ''),
    toJunction: String(edge.to ?? ''),
    laneCount: lanes.length,
    points,
  }]
})

const uniquePoints = []
const pointIndexes = new Map()
const roadPointIndexes = roads.map((road) => road.points.map(([x, y]) => {
  const key = `${x},${y}`
  const existing = pointIndexes.get(key)
  if (existing !== undefined) return existing
  const index = uniquePoints.length
  uniquePoints.push([x, y])
  pointIndexes.set(key, index)
  return index
}))
const converted = convertSumoCoordinates(uniquePoints)
const bounds = {
  west: Infinity,
  south: Infinity,
  east: -Infinity,
  north: -Infinity,
}
const features = roads.map((road, roadIndex) => {
  const coordinates = roadPointIndexes[roadIndex].map((index) => {
    const [longitude, latitude] = converted[index]
    bounds.west = Math.min(bounds.west, longitude)
    bounds.south = Math.min(bounds.south, latitude)
    bounds.east = Math.max(bounds.east, longitude)
    bounds.north = Math.max(bounds.north, latitude)
    return [Number(longitude.toFixed(7)), Number(latitude.toFixed(7))]
  })
  return {
    type: 'Feature',
    properties: {
      edge_id: road.edgeId,
      from_junction: road.fromJunction,
      to_junction: road.toJunction,
      lane_count: road.laneCount,
    },
    geometry: { type: 'LineString', coordinates },
  }
})
const collection = {
  type: 'FeatureCollection',
  metadata: {
    schemaVersion: 1,
    source: 'data/maps/sumo/generated/network/TotalMap_20.signals.net.xml',
    sourceSha256,
    edgeCount: features.length,
    bounds,
  },
  features,
}

await writeFile(outputPath, `${JSON.stringify(collection)}\n`, 'utf8')
console.log(JSON.stringify({ outputPath, sourceSha256, edgeCount: features.length, bounds }))
