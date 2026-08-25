import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { XMLParser } from 'fast-xml-parser'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sumoDirectory = path.resolve(frontendDirectory, '../data/maps/sumo/generated')
const networkPath = path.join(sumoDirectory, 'network/TotalMap_20.signals.net.xml')
const routePaths = [
  path.join(sumoDirectory, 'traffic/global/morning_peak/routes.rou.xml'),
  path.join(sumoDirectory, 'traffic/global/off_peak/routes.rou.xml'),
  path.join(sumoDirectory, 'traffic/global/evening_peak/routes.rou.xml'),
]
const manifestDirectory = path.join(frontendDirectory, 'public/intersections/v3')
const outputPath = path.join(frontendDirectory, 'src/assets/safe-lane-closures.json')
const preferredLaneByIntersection = new Map([
  ['demo_1', '-56384_1'],
  ['demo_6', '-50334_1'],
])

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '',
  parseAttributeValue: false,
})

function asArray(value) {
  if (value == null) return []
  return Array.isArray(value) ? value : [value]
}

function sha256(content) {
  return createHash('sha256').update(content).digest('hex')
}

function routeEdges(document) {
  return [
    ...asArray(document.routes?.vehicle),
    ...asArray(document.routes?.flow),
    ...asArray(document.routes?.trip),
  ].flatMap((entry) => {
    const route = asArray(entry.route)[0]
    const edges = String(route?.edges ?? entry.edges ?? '').trim()
    return edges ? [edges.split(/\s+/)] : []
  })
}

function movementKey(fromEdge, toEdge) {
  return `${fromEdge}\u0000${toEdge}`
}

function laneCanCarryRoadVehicles(lane) {
  return lane.kind !== 'pedestrian'
}

function compareCandidates(left, right) {
  return right.minimumAlternativeLaneCount - left.minimumAlternativeLaneCount
    || right.alternativeConnectionCount - left.alternativeConnectionCount
    || left.laneIndex - right.laneIndex
    || left.laneId.localeCompare(right.laneId)
}

async function main() {
  const networkContent = await readFile(networkPath, 'utf8')
  const network = parser.parse(networkContent)
  const connections = asArray(network.net?.connection)
    .filter((connection) => !String(connection.from ?? '').startsWith(':'))
  const connectionsByMovement = new Map()
  for (const connection of connections) {
    const key = movementKey(String(connection.from), String(connection.to))
    const values = connectionsByMovement.get(key) ?? []
    values.push({
      fromLane: Number(connection.fromLane),
      toLane: Number(connection.toLane),
    })
    connectionsByMovement.set(key, values)
  }

  const usedMovements = new Map()
  const routeSources = []
  for (const routePath of routePaths) {
    const content = await readFile(routePath, 'utf8')
    routeSources.push({
      path: path.relative(frontendDirectory, routePath).replaceAll('\\', '/'),
      sha256: sha256(content),
    })
    const document = parser.parse(content)
    for (const edges of routeEdges(document)) {
      for (let index = 1; index < edges.length; index += 1) {
        const fromEdge = edges[index - 1]
        const toEdge = edges[index]
        const key = movementKey(fromEdge, toEdge)
        usedMovements.set(key, (usedMovements.get(key) ?? 0) + 1)
      }
    }
  }

  const intersections = {}
  for (let index = 1; index <= 20; index += 1) {
    const intersectionId = `demo_${index}`
    const manifest = JSON.parse(await readFile(
      path.join(manifestDirectory, intersectionId, 'manifest.json'),
      'utf8',
    ))
    const candidates = manifest.edges
      .filter((edge) => edge.incoming)
      .flatMap((edge) => edge.lanes
        .filter(laneCanCarryRoadVehicles)
        .map((lane) => {
          const routeMovements = [...usedMovements.entries()]
            .filter(([key]) => key.startsWith(`${edge.id}\u0000`))
            .map(([key, routeCount]) => {
              const [, toEdge] = key.split('\u0000')
              const movementConnections = connectionsByMovement.get(key) ?? []
              const laneServesMovement = movementConnections.some(
                (connection) => connection.fromLane === Number(lane.index),
              )
              const alternativeLanes = [...new Set(movementConnections
                .filter((connection) => connection.fromLane !== Number(lane.index))
                .map((connection) => connection.fromLane))]
              return {
                toEdge,
                routeCount,
                laneServesMovement,
                alternativeLaneCount: alternativeLanes.length,
              }
            })
            .filter((movement) => movement.laneServesMovement)
          const unsafeMovements = routeMovements
            .filter((movement) => movement.alternativeLaneCount === 0)
            .map((movement) => ({
              toEdge: movement.toEdge,
              routeCount: movement.routeCount,
            }))
          const fallbackAlternativeCount = Math.max(0, edge.lanes.filter(laneCanCarryRoadVehicles).length - 1)
          return {
            laneId: String(lane.id),
            edgeId: String(edge.id),
            laneIndex: Number(lane.index),
            safe: unsafeMovements.length === 0,
            routeMovementCount: routeMovements.length,
            minimumAlternativeLaneCount: routeMovements.length > 0
              ? Math.min(...routeMovements.map((movement) => movement.alternativeLaneCount))
              : fallbackAlternativeCount,
            alternativeConnectionCount: routeMovements.reduce(
              (total, movement) => total + movement.alternativeLaneCount,
              0,
            ),
            unsafeMovements,
          }
        }))
    const safeCandidates = candidates.filter((candidate) => candidate.safe).sort(compareCandidates)
    const preferredLaneId = preferredLaneByIntersection.get(intersectionId)
    const selected = safeCandidates.find((candidate) => candidate.laneId === preferredLaneId)
      ?? safeCandidates[0]
    intersections[intersectionId] = {
      selectedLaneIds: selected ? [selected.laneId] : [],
      candidates,
      unavailableReason: selected
        ? null
        : '当前三套固定路线下没有可安全封闭的进口车道',
    }
  }

  for (const [intersectionId, expectedLaneId] of preferredLaneByIntersection) {
    if (!intersections[intersectionId].selectedLaneIds.includes(expectedLaneId)) {
      throw new Error(`${intersectionId} does not retain required safe lane ${expectedLaneId}`)
    }
  }
  for (const intersectionId of ['demo_7', 'demo_10']) {
    if (intersections[intersectionId].selectedLaneIds.length > 0) {
      throw new Error(`${intersectionId} unexpectedly has a topology-safe lane closure candidate`)
    }
  }

  await mkdir(path.dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify({
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    sourcePath: path.relative(frontendDirectory, networkPath).replaceAll('\\', '/'),
    sourceSha256: sha256(networkContent),
    routeSources,
    selectionRule: [
      'no exclusive fixed-route movement',
      'most alternative lanes',
      'lowest lane index',
      'stable lane id',
    ],
    intersections,
  }, null, 2)}\n`, 'utf8')
  console.log(`Wrote ${path.relative(frontendDirectory, outputPath)}`)
}

await main()
