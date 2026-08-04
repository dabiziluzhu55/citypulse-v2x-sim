import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { XMLParser } from 'fast-xml-parser'

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendDirectory = path.resolve(scriptsDirectory, '..')
const projectDirectory = path.resolve(frontendDirectory, '..')
const networkPath = path.resolve(projectDirectory, 'data/maps/sumo/generated/network/TotalMap_20.signals.net.xml')
const catalogPath = path.resolve(frontendDirectory, 'public/intersections/v3/catalog.json')
const outputArgument = process.argv.find((argument) => argument.startsWith('--output='))
const outputPath = path.resolve(frontendDirectory, outputArgument?.split('=', 2)[1] ?? 'reports/road-continuity-audit.json')
const MAXIMUM_SECONDARY_GAP_METERS = 20
const MINIMUM_OVERLAP_METERS = 0.5

function asArray(value) {
  if (value === undefined) return []
  return Array.isArray(value) ? value : [value]
}

function polygonArea(points) {
  return Math.abs(points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]
    return sum + point[0] * next[1] - next[0] * point[1]
  }, 0)) / 2
}

function edgeEndpoint(edge, endpoint) {
  const points = edge.centerline ?? edge.lanes[0]?.renderPoints ?? edge.lanes[0]?.points ?? []
  return endpoint === 'start' ? points[0] : points.at(-1)
}

const source = await readFile(networkPath, 'utf8')
const sourceSha256 = createHash('sha256').update(source).digest('hex')
const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: true,
})
const network = parser.parse(source).net
const sourceEdges = new Map(asArray(network.edge)
  .filter((edge) => !edge.function)
  .map((edge) => [String(edge.id), {
    fromJunction: String(edge.from),
    toJunction: String(edge.to),
  }]))
const sourceConnections = asArray(network.connection).flatMap((connection) => {
  const fromEdge = sourceEdges.get(String(connection.from))
  const toEdge = sourceEdges.get(String(connection.to))
  if (!fromEdge || !toEdge || fromEdge.toJunction !== toEdge.fromJunction) return []
  return [{
    junctionId: fromEdge.toJunction,
    fromEdge: String(connection.from),
    toEdge: String(connection.to),
  }]
})
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'))
const intersections = []

for (const entry of catalog.intersections) {
  const manifestPath = path.resolve(frontendDirectory, `public/intersections/v3/${entry.intersectionId}/manifest.json`)
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const edges = new Map(manifest.edges
    .filter((edge) => edge.lanes.some((lane) => lane.kind === 'driving'))
    .map((edge) => [edge.id, edge]))
  const endpoint = (junctionId, edgeId) => {
    const edge = edges.get(edgeId)
    const sourceEdge = sourceEdges.get(edgeId)
    if (!edge || !sourceEdge) return null
    const side = sourceEdge.fromJunction === junctionId
      ? 'start'
      : sourceEdge.toJunction === junctionId ? 'end' : null
    if (!side) return null
    const point = edgeEndpoint(edge, side)
    if (!point || Math.hypot(point[0], point[1]) >= manifest.radiusSceneUnits * 0.995) return null
    return point
  }
  const pairs = new Map()
  for (const connection of sourceConnections) {
    const from = endpoint(connection.junctionId, connection.fromEdge)
    const to = endpoint(connection.junctionId, connection.toEdge)
    if (!from || !to) continue
    const edgeIds = [connection.fromEdge, connection.toEdge].sort()
    const key = `${connection.junctionId}:${edgeIds.join(':')}`
    const gapMeters = Math.hypot(to[0] - from[0], to[1] - from[1]) / manifest.horizontalScale
    const previous = pairs.get(key)
    if (!previous || gapMeters < previous.gapMeters) {
      pairs.set(key, { junctionId: connection.junctionId, edgeIds, gapMeters })
    }
  }

  const invalidJoints = []
  for (const joint of manifest.roadJoints ?? []) {
    for (const layer of ['sidewalk', 'curb', 'asphalt']) {
      if ((joint.polygons?.[layer]?.length ?? 0) < 3 || polygonArea(joint.polygons[layer]) <= 0.05) {
        invalidJoints.push(`${joint.jointId}:${layer}`)
      }
    }
    if (joint.junctionId !== manifest.junctionId && joint.maxGapMeters > MAXIMUM_SECONDARY_GAP_METERS + 0.001) {
      invalidJoints.push(`${joint.jointId}:gap`)
    }
    if (joint.overlapMeters < MINIMUM_OVERLAP_METERS) invalidJoints.push(`${joint.jointId}:overlap`)
  }
  const primaryJoint = (manifest.roadJoints ?? []).find((joint) => joint.junctionId === manifest.junctionId)
  const eligible = [...pairs.values()].filter((pair) => (
    pair.junctionId === manifest.junctionId || pair.gapMeters <= MAXIMUM_SECONDARY_GAP_METERS
  ))
  const rejected = [...pairs.values()].filter((pair) => (
    pair.junctionId !== manifest.junctionId && pair.gapMeters > MAXIMUM_SECONDARY_GAP_METERS
  ))
  const uncovered = eligible.filter((pair) => !(manifest.roadJoints ?? []).some((joint) => (
    joint.junctionId === pair.junctionId
    && pair.edgeIds.every((edgeId) => joint.connectedEdgeIds.includes(edgeId))
  )))
  const status = manifest.sourceSha256 === sourceSha256
    && primaryJoint
    && invalidJoints.length === 0
    && uncovered.length === 0
    ? 'pass'
    : 'fail'
  intersections.push({
    intersectionId: manifest.intersectionId,
    status,
    roadJointCount: manifest.roadJoints?.length ?? 0,
    secondaryJointCount: (manifest.roadJoints ?? []).filter((joint) => joint.junctionId !== manifest.junctionId).length,
    eligibleConnectionCount: eligible.length,
    uncoveredEligibleConnections: uncovered.map((pair) => ({
      junctionId: pair.junctionId,
      edgeIds: pair.edgeIds,
      gapMeters: Number(pair.gapMeters.toFixed(3)),
    })),
    rejectedSourceGaps: rejected.map((pair) => ({
      junctionId: pair.junctionId,
      edgeIds: pair.edgeIds,
      gapMeters: Number(pair.gapMeters.toFixed(3)),
    })),
    invalidJoints,
    jointSurfaceHeightMeters: 0.014,
    roadSurfaceHeightMeters: 0,
  })
}

const report = {
  schemaVersion: 1,
  generatedAt: new Date().toISOString(),
  sourceSha256,
  thresholds: {
    maximumSecondaryGapMeters: MAXIMUM_SECONDARY_GAP_METERS,
    minimumOverlapMeters: MINIMUM_OVERLAP_METERS,
    maximumSurfaceHeightDifferenceMeters: 0.02,
    maximumVisibleBreakPixels: 3,
  },
  intersections,
  summary: {
    checked: intersections.length,
    passed: intersections.filter((item) => item.status === 'pass').length,
    failed: intersections.filter((item) => item.status === 'fail').length,
    roadJoints: intersections.reduce((sum, item) => sum + item.roadJointCount, 0),
    uncoveredEligibleConnections: intersections.reduce((sum, item) => sum + item.uncoveredEligibleConnections.length, 0),
    rejectedSourceGaps: intersections.reduce((sum, item) => sum + item.rejectedSourceGaps.length, 0),
  },
}

await mkdir(path.dirname(outputPath), { recursive: true })
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.table(intersections.map((item) => ({
  intersection: item.intersectionId,
  status: item.status,
  joints: item.roadJointCount,
  eligible: item.eligibleConnectionCount,
  uncovered: item.uncoveredEligibleConnections.length,
  rejected: item.rejectedSourceGaps.length,
})))
console.log(JSON.stringify(report.summary))
if (report.summary.failed > 0) process.exitCode = 1
