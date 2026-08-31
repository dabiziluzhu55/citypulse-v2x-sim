import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { XMLParser } from 'fast-xml-parser'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDirectory = path.resolve(frontendDirectory, '..')
const sumoDirectory = path.join(repositoryDirectory, 'data/maps/sumo/generated')
const networkPath = path.join(sumoDirectory, 'network/TotalMap_20.signals.net.xml')
const manifestDirectory = path.join(frontendDirectory, 'public/intersections/v3')
const outputPath = path.join(frontendDirectory, 'src/assets/vehicle-route-turn-index.json')
const periods = ['morning_peak', 'off_peak', 'evening_peak']

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

function connectionKey(connection) {
  return `${connection.tlsId}:${connection.linkIndex}`
}

function routeEntries(document) {
  return [...asArray(document.routes?.flow), ...asArray(document.routes?.vehicle)]
    .flatMap((entry) => {
      const route = asArray(entry.route)[0]
      const edges = String(route?.edges ?? entry.edges ?? '').trim().split(/\s+/).filter(Boolean)
      return entry.id && edges.length > 1 ? [{ flowId: String(entry.id), edges }] : []
    })
}

async function main() {
  const networkContent = await readFile(networkPath, 'utf8')
  const networkSha256 = sha256(networkContent)
  const connectionsByMovement = new Map()
  for (let index = 1; index <= 20; index += 1) {
    const intersectionId = `demo_${index}`
    const manifest = JSON.parse(await readFile(
      path.join(manifestDirectory, intersectionId, 'manifest.json'),
      'utf8',
    ))
    if (manifest.sumoHeadingTransform?.sourceSha256 !== networkSha256) {
      throw new Error(`${intersectionId} SUMO source hash does not match the current network`)
    }
    for (const connection of manifest.vehicleConnections ?? manifest.connections) {
      const movement = `${connection.fromEdge}\u0000${connection.toEdge}`
      const candidates = connectionsByMovement.get(movement) ?? []
      candidates.push({
        intersectionId,
        connectionKey: connectionKey(connection),
        motionPathKey: `route:${connectionKey(connection)}`,
        fromEdge: String(connection.fromEdge),
        fromLaneId: `${connection.fromEdge}_${connection.fromLane}`,
        fromLaneIndex: Number(connection.fromLane),
        toEdge: String(connection.toEdge),
        toLaneId: `${connection.toEdge}_${connection.toLane}`,
        toLaneIndex: Number(connection.toLane),
        viaLaneIds: (connection.viaSegments ?? [])
          .map((segment) => String(segment.laneId))
          .concat(connection.viaLaneId ? [String(connection.viaLaneId)] : []),
      })
      connectionsByMovement.set(movement, candidates)
    }
  }

  const routeSources = {}
  const edgeTable = []
  const edgeIndexes = new Map()
  const edgeIndex = (edgeId) => {
    let index = edgeIndexes.get(edgeId)
    if (index == null) {
      index = edgeTable.length
      edgeIndexes.set(edgeId, index)
      edgeTable.push(edgeId)
    }
    return index
  }
  const connectionTable = Object.fromEntries(
    [...connectionsByMovement.values()].flat().map((candidate) => [candidate.connectionKey, candidate]),
  )
  const byPeriod = {}
  for (const period of periods) {
    const routePath = path.join(sumoDirectory, `traffic/global/${period}/routes.rou.xml`)
    const routeContent = await readFile(routePath, 'utf8')
    routeSources[period] = {
      path: path.relative(repositoryDirectory, routePath).replaceAll('\\', '/'),
      sha256: sha256(routeContent),
    }
    const flows = {}
    const routes = []
    const edgeRoutes = []
    const routeIndexes = new Map()
    for (const { flowId, edges } of routeEntries(parser.parse(routeContent))) {
      const turns = []
      for (let routeIndex = 0; routeIndex < edges.length - 1; routeIndex += 1) {
        const fromEdge = edges[routeIndex]
        const toEdge = edges[routeIndex + 1]
        const candidates = connectionsByMovement.get(`${fromEdge}\u0000${toEdge}`) ?? []
        if (candidates.length === 0) continue
        turns.push([
          routeIndex,
          fromEdge,
          toEdge,
          candidates.map((candidate) => candidate.connectionKey),
        ])
      }
      if (turns.length > 0) {
        const signature = JSON.stringify([edges, turns])
        let compactRouteIndex = routeIndexes.get(signature)
        if (compactRouteIndex == null) {
          compactRouteIndex = routes.length
          routeIndexes.set(signature, compactRouteIndex)
          routes.push(turns)
          edgeRoutes.push(edges.map(edgeIndex))
        }
        flows[flowId] = compactRouteIndex
      }
    }
    byPeriod[period] = { routes, edgeRoutes, flows }
  }

  await mkdir(path.dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify({
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    networkSource: {
      path: path.relative(repositoryDirectory, networkPath).replaceAll('\\', '/'),
      sha256: networkSha256,
    },
    routeSources,
    edgeTable,
    connections: connectionTable,
    periods: byPeriod,
  })}\n`, 'utf8')
  console.log(`Wrote ${path.relative(frontendDirectory, outputPath)}`)
}

await main()
