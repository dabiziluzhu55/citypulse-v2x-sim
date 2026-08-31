import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { XMLParser } from 'fast-xml-parser'
import { projectBd09ToWebMercator, wgs84ToBd09 } from '../src/mapv/sceneCoordinates.ts'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDirectory = path.resolve(frontendDirectory, '..')
const networkPath = path.join(
  repositoryDirectory,
  'data/maps/sumo/generated/network/TotalMap_20.signals.net.xml',
)
const converterPath = path.join(frontendDirectory, 'scripts/convert-sumo-coordinates.py')
const manifestDirectory = path.join(frontendDirectory, 'public/intersections/v3')
const catalogPath = path.join(manifestDirectory, 'catalog.json')

function asArray(value) {
  if (value == null) return []
  return Array.isArray(value) ? value : [value]
}

function polylineLength(points) {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
}

function alignPolylineEndpoints(points, start, end) {
  if (points.length < 2) return [start, end]
  const startOffset = [start[0] - points[0][0], start[1] - points[0][1]]
  const endOffset = [end[0] - points.at(-1)[0], end[1] - points.at(-1)[1]]
  const totalLength = polylineLength(points)
  let distance = 0
  return points.map((point, index) => {
    if (index > 0) distance += Math.hypot(
      point[0] - points[index - 1][0],
      point[1] - points[index - 1][1],
    )
    const progress = totalLength > 1e-6 ? distance / totalLength : index / (points.length - 1)
    return [
      point[0] + startOffset[0] * (1 - progress) + endOffset[0] * progress,
      point[1] + startOffset[1] * (1 - progress) + endOffset[1] * progress,
    ]
  })
}

function joinPolylines(polylines) {
  const result = []
  for (const points of polylines) {
    for (const point of points) {
      const previous = result.at(-1)
      if (previous && Math.hypot(point[0] - previous[0], point[1] - previous[1]) < 0.001) continue
      result.push(point)
    }
  }
  return result
}

function splitAlignedSegments(segments, aligned) {
  const source = joinPolylines(segments.map((segment) => segment.points))
  const result = []
  let sourceCursor = 0
  for (const segment of segments) {
    const points = []
    for (const sourcePoint of segment.points) {
      while (
        sourceCursor < source.length - 1
        && Math.hypot(
          source[sourceCursor][0] - sourcePoint[0],
          source[sourceCursor][1] - sourcePoint[1],
        ) > 0.001
      ) sourceCursor += 1
      points.push(aligned[sourceCursor] ?? aligned.at(-1))
    }
    result.push(points)
  }
  return result
}

function convertSumoCoordinates(points) {
  const commands = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']
  const failures = []
  for (const command of commands) {
    const result = spawnSync(command, [converterPath, networkPath], {
      cwd: frontendDirectory,
      encoding: 'utf8',
      input: JSON.stringify(points),
      maxBuffer: 64 * 1024 * 1024,
    })
    if (result.status === 0) return JSON.parse(result.stdout)
    failures.push(result.stderr?.trim() || result.error?.message || `exit ${result.status}`)
  }
  throw new Error(`SUMO coordinate conversion failed: ${failures.join('; ')}`)
}

const networkSource = await readFile(networkPath, 'utf8')
const networkSha256 = createHash('sha256').update(networkSource).digest('hex')
const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: false,
})
const network = parser.parse(networkSource).net
const edges = asArray(network.edge)
const connections = asArray(network.connection)
const lanesById = new Map(edges.flatMap((edge) => asArray(edge.lane).map((lane) => [
  String(lane.id),
  { edgeId: String(edge.id), lane },
])))

function collectViaSegments(connection) {
  const result = []
  const seen = new Set()
  let laneId = connection.via ? String(connection.via) : ''
  const targetEdge = String(connection.to)
  const targetLane = Number(connection.toLane)
  while (laneId && !seen.has(laneId)) {
    seen.add(laneId)
    const metadata = lanesById.get(laneId)
    if (!metadata) break
    const points = String(metadata.lane.shape ?? '').trim().split(/\s+/).flatMap((pair) => {
      const [x, y] = pair.split(',').map(Number)
      return Number.isFinite(x) && Number.isFinite(y) ? [[x, y]] : []
    })
    if (points.length < 2) break
    result.push({ laneId, absolutePoints: points })
    const continuation = connections.find((candidate) => (
      String(candidate.from) === metadata.edgeId
      && Number(candidate.fromLane) === Number(metadata.lane.index)
      && String(candidate.to) === targetEdge
      && Number(candidate.toLane) === targetLane
    ))
    laneId = continuation?.via ? String(continuation.via) : ''
  }
  return result
}

const manifests = []
const conversionPoints = []
for (let index = 1; index <= 20; index += 1) {
  const manifestPath = path.join(manifestDirectory, `demo_${index}`, 'manifest.json')
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const tlsIds = new Set((manifest.tlsIds ?? [manifest.tlsId]).filter(Boolean).map(String))
  const vehicleConnections = connections
    .filter((connection) => tlsIds.has(String(connection.tl)) && connection.linkIndex != null)
    .map((connection) => {
      const segments = collectViaSegments(connection)
      for (const segment of segments) {
        segment.conversionStart = conversionPoints.length
        conversionPoints.push(...segment.absolutePoints)
      }
      return { connection, segments }
    })
  manifests.push({ manifestPath, manifest, vehicleConnections })
}

const converted = convertSumoCoordinates(conversionPoints)
for (const { manifestPath, manifest, vehicleConnections } of manifests) {
  const origin = manifest.origin.webMercator
    ?? projectBd09ToWebMercator(manifest.origin.bd09)
  const edgeById = new Map(manifest.edges.map((edge) => [edge.id, edge]))
  manifest.vehicleConnections = vehicleConnections.flatMap(({ connection, segments }) => {
    const fromEdge = edgeById.get(String(connection.from))
    const toEdge = edgeById.get(String(connection.to))
    const fromLane = fromEdge?.lanes.find((lane) => lane.index === Number(connection.fromLane))
    const toLane = toEdge?.lanes.find((lane) => lane.index === Number(connection.toLane))
    if (!fromLane?.renderPoints?.length || !toLane?.renderPoints?.length) return []
    const projectedSegments = segments.map((segment) => ({
      laneId: segment.laneId,
      points: converted.slice(
        segment.conversionStart,
        segment.conversionStart + segment.absolutePoints.length,
      ).map(([longitude, latitude]) => {
        const projected = projectBd09ToWebMercator(wgs84ToBd09(longitude, latitude))
        return [projected[0] - origin[0], projected[1] - origin[1]]
      }),
    }))
    const sourcePath = joinPolylines(projectedSegments.map((segment) => segment.points))
    const start = fromEdge.incoming ? fromLane.renderPoints.at(-1) : fromLane.renderPoints[0]
    const end = toEdge.incoming ? toLane.renderPoints.at(-1) : toLane.renderPoints[0]
    const renderPath = alignPolylineEndpoints(sourcePath.length >= 2 ? sourcePath : [start, end], start, end)
    const renderSegments = projectedSegments.length
      ? splitAlignedSegments(projectedSegments, renderPath)
      : []
    const direction = ['s', 'l', 'r', 't'].includes(String(connection.dir).toLowerCase())
      ? String(connection.dir).toLowerCase()
      : 's'
    return [{
      tlsId: String(connection.tl),
      linkIndex: Number(connection.linkIndex),
      fromEdge: String(connection.from),
      fromLane: Number(connection.fromLane),
      toEdge: String(connection.to),
      toLane: Number(connection.toLane),
      direction,
      directionLabel: ({ s: 'through', l: 'left', r: 'right', t: 'u-turn' })[direction],
      ...(projectedSegments.length ? {
        viaLaneId: projectedSegments[0].laneId,
        viaPoints: sourcePath,
        viaSegments: projectedSegments.map((segment, segmentIndex) => ({
          ...segment,
          renderPoints: renderSegments[segmentIndex],
          vehicleGuidePoints: renderSegments[segmentIndex],
        })),
        renderPoints: renderPath,
        vehicleGuidePoints: renderPath,
      } : {}),
    }]
  }).sort((left, right) => left.tlsId.localeCompare(right.tlsId) || left.linkIndex - right.linkIndex)
  manifest.vehicleConnectionSourceSha256 = networkSha256
  manifest.visualRoadSourceSha256 ??= manifest.sourceSha256
  manifest.vehicleGeometryGeneration = {
    schemaVersion: 1,
    networkSourceSha256: networkSha256,
    headingSourceSha256: manifest.sumoHeadingTransform?.sourceSha256 ?? '',
    connectionSourceSha256: networkSha256,
    connectionCount: manifest.vehicleConnections.length,
  }
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
}

const catalog = JSON.parse(await readFile(catalogPath, 'utf8'))
catalog.vehicleGeometryGeneration = {
  schemaVersion: 1,
  networkSourceSha256: networkSha256,
  intersectionCount: manifests.length,
  connectionCount: manifests.reduce(
    (total, item) => total + item.manifest.vehicleConnections.length,
    0,
  ),
}
await writeFile(catalogPath, `${JSON.stringify(catalog, null, 2)}\n`, 'utf8')

console.log(`Wrote ${manifests.length} manifests with ${manifests.reduce(
  (total, item) => total + item.manifest.vehicleConnections.length,
  0,
)} SUMO-authoritative vehicle connections`)
