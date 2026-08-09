import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { XMLParser } from 'fast-xml-parser'
import { projectBd09ToWebMercator, wgs84ToBd09 } from '../src/mapv/sceneCoordinates.ts'
import { rebuildRoadEdgeGeometry } from '../src/mapv/realistic/intersectionRoadGeometry.ts'
import { buildRoadJoints } from '../src/mapv/realistic/intersectionRoadJoints.ts'
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
// SUMO's projected unit is smaller than a BD-09 WebMercator scene unit here.
// Read a wider source window, then apply the exact 520 m render crop after reprojection.
const SUMO_SELECTION_RADIUS_UNITS = 760
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

async function writeFileWithRetry(target, content, attempts = 5) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await writeFile(target, content, 'utf8')
      return
    } catch (cause) {
      const code = cause instanceof Error && 'code' in cause ? cause.code : ''
      if (!['UNKNOWN', 'EBUSY', 'EPERM'].includes(String(code)) || attempt === attempts) throw cause
      await delay(80 * attempt)
    }
  }
}

function polylineLength(points) {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
}

function joinPolylines(polylines) {
  const joined = []
  for (const points of polylines) {
    for (const point of points) {
      const previous = joined.at(-1)
      if (previous && Math.hypot(point[0] - previous[0], point[1] - previous[1]) <= 0.001) continue
      joined.push(point)
    }
  }
  return joined
}

function alignPolylineEndpoints(points, start, end) {
  if (points.length < 2) return [start, end]
  const startOffset = [start[0] - points[0][0], start[1] - points[0][1]]
  const last = points.at(-1)
  const endOffset = [end[0] - last[0], end[1] - last[1]]
  const totalLength = polylineLength(points)
  let distance = 0
  return points.map((point, index) => {
    if (index > 0) {
      distance += Math.hypot(
        point[0] - points[index - 1][0],
        point[1] - points[index - 1][1],
      )
    }
    const progress = totalLength > 1e-6 ? distance / totalLength : index / (points.length - 1)
    return [
      point[0] + startOffset[0] * (1 - progress) + endOffset[0] * progress,
      point[1] + startOffset[1] * (1 - progress) + endOffset[1] * progress,
    ]
  })
}

function alignViaSegments(segments, start, end) {
  const source = joinPolylines(segments.map((segment) => segment.points))
  const aligned = alignPolylineEndpoints(source, start, end)
  const renderSegments = []
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
      ) {
        sourceCursor += 1
      }
      points.push(aligned[sourceCursor] ?? aligned.at(-1))
    }
    renderSegments.push(roundShape(points))
  }
  return renderSegments
}

function splitPolyline(points, count) {
  const lengths = [0]
  for (let index = 1; index < points.length; index += 1) {
    lengths.push(lengths.at(-1) + Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1],
    ))
  }
  const total = lengths.at(-1) || 1
  const sample = (progress) => {
    const target = total * progress
    let index = 1
    while (index < lengths.length - 1 && lengths[index] < target) index += 1
    const segment = lengths[index] - lengths[index - 1]
    const ratio = segment > 1e-9 ? (target - lengths[index - 1]) / segment : 0
    return [
      points[index - 1][0] + (points[index][0] - points[index - 1][0]) * ratio,
      points[index - 1][1] + (points[index][1] - points[index - 1][1]) * ratio,
    ]
  }
  return Array.from({ length: count }, (_, index) => [
    sample(index / count),
    sample((index + 1) / count),
  ])
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
const allEdges = asArray(net.edge)
const edges = allEdges.filter((edge) => !edge.function)
const lanesById = new Map(allEdges.flatMap((edge) => (
  asArray(edge.lane).map((lane) => [String(lane.id), {
    edgeId: String(edge.id),
    edgeFunction: edge.function ? String(edge.function) : undefined,
    lane,
  }])
)))
const networkConnections = asArray(net.connection)
const networkConnectionsBySignalLink = new Map(networkConnections
  .filter((connection) => connection.tl !== undefined && connection.linkIndex !== undefined)
  .map((connection) => [`${connection.tl}:${connection.linkIndex}`, connection]))
const tlLogics = new Map(asArray(net.tlLogic).map((logic) => [String(logic.id), logic]))
const sourceSha256 = createHash('sha256').update(source).digest('hex')
const pending = []

const existingManifests = new Map()
for (const intersectionId of Object.keys(mapping)) {
  const existing = await readFile(
    path.resolve(outputDirectory, intersectionId, 'manifest.json'),
    'utf8',
  ).then(JSON.parse).catch(() => null)
  if (existing) existingManifests.set(intersectionId, existing)
}

function topologyFromExistingManifest(manifest) {
  if (!manifest) return null
  return {
    tls_ids: manifest.tlsIds ?? (manifest.tlsId ? [manifest.tlsId] : []),
    templates: manifest.phaseTemplates ?? {},
    connections: asArray(manifest.connections).map((connection) => ({
      tls_id: connection.tlsId,
      link_index: connection.linkIndex,
      from_edge: connection.fromEdge,
      from_lane: connection.fromLane,
      to_edge: connection.toEdge,
      to_lane: connection.toLane,
      direction: connection.direction,
    })),
  }
}

function collectViaSegments(connection, sumoOrigin) {
  const result = []
  const seen = new Set()
  let viaLaneId = connection?.via ? String(connection.via) : ''
  const targetEdge = String(connection?.to ?? '')
  const targetLane = Number(connection?.toLane ?? 0)
  while (viaLaneId && !seen.has(viaLaneId)) {
    seen.add(viaLaneId)
    const metadata = lanesById.get(viaLaneId)
    if (!metadata) break
    const points = roundShape(toLocalShape(parseShape(String(metadata.lane.shape)), sumoOrigin))
    if (points.length < 2) break
    result.push({ laneId: viaLaneId, points })
    const laneIndex = Number(metadata.lane.index)
    const continuation = networkConnections.find((candidate) => (
      String(candidate.from) === metadata.edgeId
      && Number(candidate.fromLane) === laneIndex
      && String(candidate.to) === targetEdge
      && Number(candidate.toLane) === targetLane
    ))
    viaLaneId = continuation?.via ? String(continuation.via) : ''
  }
  return result
}

const availableIntersectionIds = Object.keys(mapping)
  .filter((intersectionId) => (
    tlsManifest.intersections?.[intersectionId]
    || topologyFromExistingManifest(existingManifests.get(intersectionId))
  ))
  .filter((intersectionId) => !requestedIntersectionId || intersectionId === requestedIntersectionId)
  .sort(numericDemoOrder)
if (requestedIntersectionId && !availableIntersectionIds.includes(requestedIntersectionId)) {
  throw new Error(`TLS manifest is missing ${requestedIntersectionId}`)
}

for (const intersectionId of availableIntersectionIds) {
  const location = mapping[intersectionId]
  const topology = tlsManifest.intersections?.[intersectionId]
    ?? topologyFromExistingManifest(existingManifests.get(intersectionId))
  const junctionId = String(location.junction_id)
  const junction = junctions.get(junctionId)
  if (!junction) throw new Error(`SUMO network is missing junction ${junctionId}`)
  const sumoOrigin = [Number(junction.x), Number(junction.y)]
  const renderedEdges = edges.map((edge) => ({
    id: String(edge.id),
    fromJunction: String(edge.from),
    toJunction: String(edge.to),
    incoming: String(edge.to) === junctionId,
    incident: String(edge.from) === junctionId || String(edge.to) === junctionId,
    lanes: asArray(edge.lane).map((lane) => ({
      id: String(lane.id),
      index: Number(lane.index),
      kind: laneKind(lane),
      width: Number(lane.width) || 3.2,
      speed: Number(lane.speed) || 13.9,
      points: roundShape(cropPolylineToRadius(
        toLocalShape(parseShape(String(lane.shape)), sumoOrigin),
        SUMO_SELECTION_RADIUS_UNITS,
      )),
    })).filter((lane) => lane.points.length >= 2).sort((a, b) => a.index - b.index),
  })).filter((edge) => edge.lanes.length > 0).sort((a, b) => a.id.localeCompare(b.id))
  const renderedJunctionIds = new Set(renderedEdges
    .filter((edge) => edge.lanes.some((lane) => lane.kind === 'driving'))
    .flatMap((edge) => [edge.fromJunction, edge.toJunction]))
  const authoritativeJunctions = [...renderedJunctionIds].flatMap((candidateId) => {
    if (candidateId === junctionId) return []
    const candidate = junctions.get(candidateId)
    const shape = parseShape(String(candidate?.shape ?? ''))
    const internalLaneIds = String(candidate?.intLanes ?? '')
      .split(/\s+/)
      .filter(Boolean)
      .filter((laneId) => {
        const metadata = lanesById.get(laneId)
        return metadata && laneKind(metadata.lane) === 'driving'
      })
    if (!candidate || shape.length < 3 || internalLaneIds.length === 0) return []
    return [{
      junctionId: candidateId,
      internalLaneIds,
      points: roundShape(toLocalShape(shape, sumoOrigin)),
    }]
  })

  const connections = asArray(topology.connections).map((connection) => {
    const tlsId = String(connection.tls_id)
    const linkIndex = Number(connection.link_index)
    const networkConnection = networkConnectionsBySignalLink.get(`${tlsId}:${linkIndex}`)
    const viaSegments = collectViaSegments(networkConnection, sumoOrigin)
    const viaLaneId = viaSegments[0]?.laneId
    const viaPoints = viaSegments.length ? joinPolylines(viaSegments.map((segment) => segment.points)) : undefined
    return {
      tlsId,
      linkIndex,
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
      viaLaneId,
      viaPoints,
      viaSegments: viaSegments.length ? viaSegments : undefined,
    }
  }).sort((a, b) => a.tlsId.localeCompare(b.tlsId) || a.linkIndex - b.linkIndex)

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
    authoritativeJunctions,
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
  item.authoritativeJunctions.forEach((junction) => {
    junction.points.forEach((point, index) => registerPoint(
      [sumoOrigin[0] + point[0], sumoOrigin[1] + point[1]],
      (value) => { junction.wgs84 ??= []; junction.wgs84[index] = value },
    ))
  })
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      lane.points.forEach((point, index) => registerPoint(
        [sumoOrigin[0] + point[0], sumoOrigin[1] + point[1]],
        (value) => { lane.wgs84 ??= []; lane.wgs84[index] = value },
      ))
    }
  }
  for (const connection of manifest.connections) {
    connection.viaSegments?.forEach((segment) => {
      segment.points.forEach((point, index) => registerPoint(
        [sumoOrigin[0] + point[0], sumoOrigin[1] + point[1]],
        (value) => { segment.wgs84 ??= []; segment.wgs84[index] = value },
      ))
    })
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
  generatedAt: requestedIntersectionId && existingCatalog.generatedAt
    ? existingCatalog.generatedAt
    : new Date().toISOString(),
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
  const authoritativeJunctions = item.authoritativeJunctions.map((junction) => ({
    junctionId: junction.junctionId,
    internalLaneIds: junction.internalLaneIds,
    shape: roundShape(junction.wgs84.map((point) => {
      const projected = toWebMercator(point)
      return [projected[0] - originPlane[0], projected[1] - originPlane[1]]
    })),
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
    edge.lanes = edge.lanes.filter((lane) => lane.points.length >= 2)
    if (edge.lanes.length === 0) continue
    const rebuilt = rebuildRoadEdgeGeometry(edge.lanes)
    edge.centerline = roundShape(rebuilt.centerline)
    edge.roadWidth = Number(rebuilt.roadWidth.toFixed(3))
    edge.lanes.forEach((lane, index) => {
      lane.renderPoints = roundShape(rebuilt.renderPoints[index])
    })
  }
  manifest.edges = manifest.edges.filter((edge) => edge.lanes.length > 0)
  for (const connection of manifest.connections) {
    for (const segment of connection.viaSegments ?? []) {
      if (!segment.wgs84?.length) continue
      segment.points = roundShape(segment.wgs84.map((point) => {
        const projected = toWebMercator(point)
        return [projected[0] - originPlane[0], projected[1] - originPlane[1]]
      }))
      delete segment.wgs84
    }
    connection.viaPoints = connection.viaSegments?.length
      ? roundShape(joinPolylines(connection.viaSegments.map((segment) => segment.points)))
      : connection.viaPoints
    const fromEdge = manifest.edges.find((edge) => edge.id === connection.fromEdge)
    const toEdge = manifest.edges.find((edge) => edge.id === connection.toEdge)
    const fromLane = fromEdge?.lanes.find((lane) => lane.index === connection.fromLane)
    const toLane = toEdge?.lanes.find((lane) => lane.index === connection.toLane)
    if (!connection.viaSegments?.length || !fromLane?.renderPoints?.length || !toLane?.renderPoints?.length) {
      if (connection.viaLaneId) {
        console.warn(
          `[intersection-generator] ${manifest.intersectionId} ${connection.tlsId}:${connection.linkIndex} `
          + `cannot align internal lane (from=${Boolean(fromLane)}, to=${Boolean(toLane)})`,
        )
        delete connection.viaLaneId
        delete connection.viaPoints
        delete connection.viaSegments
      }
      continue
    }
    const start = fromEdge.incoming ? fromLane.renderPoints.at(-1) : fromLane.renderPoints[0]
    const end = toEdge.incoming ? toLane.renderPoints.at(-1) : toLane.renderPoints[0]
    let renderSegments = alignViaSegments(connection.viaSegments, start, end)
    if (renderSegments.some((points) => points.length < 2 || polylineLength(points) < 0.001)) {
      const alignedFallback = alignPolylineEndpoints(connection.viaPoints ?? [start, end], start, end)
      renderSegments = splitPolyline(
        alignedFallback.length >= 2 ? alignedFallback : [start, end],
        connection.viaSegments.length,
      )
    }
    connection.viaSegments.forEach((segment, index) => {
      segment.renderPoints = renderSegments[index]
    })
    connection.renderPoints = roundShape(joinPolylines(renderSegments))
  }

  const renderedEdgeMap = new Map(manifest.edges.map((edge) => [edge.id, edge]))
  const endpoints = manifest.edges.flatMap((edge) => {
    const centerline = edge.centerline ?? []
    if (centerline.length < 2) return []
    const candidates = [
      { edgeId: edge.id, junctionId: edge.fromJunction, endpoint: 'start', point: centerline[0] },
      { edgeId: edge.id, junctionId: edge.toJunction, endpoint: 'end', point: centerline.at(-1) },
    ]
    return candidates
      .filter((candidate) => (
        candidate.junctionId
        && Math.hypot(candidate.point[0], candidate.point[1]) < manifest.radiusSceneUnits * 0.995
      ))
      .map(({ point: _point, ...candidate }) => candidate)
  })
  const jointConnections = networkConnections.flatMap((connection) => {
    const fromEdge = renderedEdgeMap.get(String(connection.from))
    const toEdge = renderedEdgeMap.get(String(connection.to))
    if (!fromEdge || !toEdge || fromEdge.toJunction !== toEdge.fromJunction) return []
    return [{
      junctionId: fromEdge.toJunction,
      fromEdge: fromEdge.id,
      toEdge: toEdge.id,
    }]
  })
  manifest.roadJoints = buildRoadJoints({
    edges: manifest.edges,
    endpoints,
    connections: jointConnections,
    authoritativeJunctions,
    primaryJunctionId: manifest.junctionId,
    primaryJunctionShape: manifest.junctionShape,
    horizontalScale: manifest.horizontalScale,
    maximumSecondaryGapMeters: 20,
    overlapMeters: 0.5,
  }).map((joint) => ({
    ...joint,
    polygons: {
      sidewalk: roundShape(joint.polygons.sidewalk),
      curb: roundShape(joint.polygons.curb),
      asphalt: roundShape(joint.polygons.asphalt),
    },
  }))
  manifest.edges.forEach((edge) => {
    delete edge.fromJunction
    delete edge.toJunction
  })

  const errors = validateIntersectionManifest(manifest)
  if (errors.length > 0) {
    throw new Error(`${manifest.intersectionId} manifest is invalid:\n- ${errors.join('\n- ')}`)
  }
  const intersectionDirectory = path.resolve(outputDirectory, manifest.intersectionId)
  await mkdir(intersectionDirectory, { recursive: true })
  await writeFileWithRetry(
    path.resolve(intersectionDirectory, 'manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`,
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

await writeFileWithRetry(
  path.resolve(outputDirectory, 'catalog.json'),
  `${JSON.stringify(catalog, null, 2)}\n`,
)
console.log(`Updated ${pending.length} projection-correct intersections; catalog contains ${catalog.intersections.length}`)
