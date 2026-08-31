import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { XMLParser } from 'fast-xml-parser'

const frontendDirectory = new URL('../', import.meta.url)
const networkUrl = new URL('../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml', import.meta.url)
const routeUrls = [
  new URL('../../data/maps/sumo/generated/traffic/global/morning_peak/routes.rou.xml', import.meta.url),
  new URL('../../data/maps/sumo/generated/traffic/global/off_peak/routes.rou.xml', import.meta.url),
  new URL('../../data/maps/sumo/generated/traffic/global/evening_peak/routes.rou.xml', import.meta.url),
]
const catalog = JSON.parse(await readFile(
  new URL('./src/assets/safe-lane-closures.json', frontendDirectory),
  'utf8',
))
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

function routeEdgeSequences(document) {
  return [
    ...asArray(document.routes?.vehicle),
    ...asArray(document.routes?.flow),
    ...asArray(document.routes?.trip),
  ].flatMap((entry) => {
    const edges = String(asArray(entry.route)[0]?.edges ?? entry.edges ?? '').trim()
    return edges ? [edges.split(/\s+/)] : []
  })
}

test('matches the current SUMO network and all three fixed route sources', async () => {
  const networkContent = await readFile(networkUrl, 'utf8')
  assert.equal(catalog.sourceSha256, sha256(networkContent))
  assert.equal(catalog.routeSources.length, routeUrls.length)
  for (let index = 0; index < routeUrls.length; index += 1) {
    assert.equal(catalog.routeSources[index].sha256, sha256(await readFile(routeUrls[index], 'utf8')))
  }
})

test('keeps every fixed-route movement connected after applying each selected closure', async () => {
  const network = parser.parse(await readFile(networkUrl, 'utf8'))
  const connections = asArray(network.net?.connection)
    .filter((connection) => !String(connection.from ?? '').startsWith(':'))
  const usedMovements = new Set()
  for (const routeUrl of routeUrls) {
    const routes = parser.parse(await readFile(routeUrl, 'utf8'))
    for (const edges of routeEdgeSequences(routes)) {
      for (let index = 1; index < edges.length; index += 1) {
        usedMovements.add(`${edges[index - 1]}\u0000${edges[index]}`)
      }
    }
  }

  for (const [intersectionId, entry] of Object.entries(catalog.intersections)) {
    for (const selectedLaneId of entry.selectedLaneIds) {
      const candidate = entry.candidates.find((item) => item.laneId === selectedLaneId)
      assert.ok(candidate, `${intersectionId}:${selectedLaneId} is absent from the candidate catalog`)
      assert.equal(candidate.safe, true, `${intersectionId}:${selectedLaneId} is not safe`)
      assert.deepEqual(candidate.unsafeMovements, [])
      for (const movement of usedMovements) {
        const [fromEdge, toEdge] = movement.split('\u0000')
        if (fromEdge !== candidate.edgeId) continue
        const movementConnections = connections.filter((connection) => (
          String(connection.from) === fromEdge && String(connection.to) === toEdge
        ))
        if (!movementConnections.some((connection) => Number(connection.fromLane) === candidate.laneIndex)) continue
        assert.ok(
          movementConnections.some((connection) => Number(connection.fromLane) !== candidate.laneIndex),
          `${intersectionId}:${selectedLaneId} is the exclusive lane for ${fromEdge} -> ${toEdge}`,
        )
      }
    }
  }
})

test('locks the reproduced route failures and unavailable construction intersections', () => {
  assert.deepEqual(catalog.intersections.demo_1.selectedLaneIds, ['-56384_1'])
  assert.ok(catalog.intersections.demo_1.candidates.some((candidate) => (
    candidate.laneId === '-56384_0'
    && candidate.unsafeMovements.some((movement) => movement.toEdge === '-56915')
  )))
  assert.deepEqual(catalog.intersections.demo_6.selectedLaneIds, ['-50334_1'])
  assert.ok(catalog.intersections.demo_6.candidates.some((candidate) => (
    candidate.laneId === '-50334_0'
    && candidate.unsafeMovements.some((movement) => movement.toEdge === '-50818')
  )))
  assert.deepEqual(
    Object.entries(catalog.intersections)
      .filter(([, entry]) => entry.selectedLaneIds.length === 0)
      .map(([intersectionId]) => intersectionId),
    ['demo_7', 'demo_10'],
  )
})
