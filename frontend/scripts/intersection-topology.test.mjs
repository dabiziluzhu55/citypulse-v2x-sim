import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildIntersectionTopologyLinks,
  intersectionTopologyBounds,
  topologyDistanceMeters,
  intersectionTopologyMaxRange,
  parseIntersectionTopologyCatalog,
} from '../src/mapv/intersectionTopology.ts'
import { parseIntersectionTopologyRoutes } from '../src/mapv/intersectionTopologyRoutes.ts'

const catalog = JSON.parse(await readFile(
  new URL('../public/intersections/v3/catalog.json', import.meta.url),
  'utf8',
))
const routeManifest = JSON.parse(await readFile(
  new URL('../public/intersections/v3/topology-routes.json', import.meta.url),
  'utf8',
))

test('loads all twenty realistic intersection markers from the frontend catalog', () => {
  const nodes = parseIntersectionTopologyCatalog(catalog)

  assert.equal(nodes.length, 20)
  assert.equal(nodes[0].intersectionId, 'demo_1')
  assert.equal(nodes.at(-1).intersectionId, 'demo_20')
})

test('connects every intersection without producing all-pairs visual noise', () => {
  const nodes = parseIntersectionTopologyCatalog(catalog)
  const links = buildIntersectionTopologyLinks(nodes)
  const linkedIds = new Set(links.flatMap((link) => [link.from.intersectionId, link.to.intersectionId]))

  assert.equal(linkedIds.size, nodes.length)
  assert.ok(links.length >= nodes.length / 2)
  assert.ok(links.length < nodes.length * 3)
  assert.ok(links.every((link) => link.distanceMeters <= 14_000))
})

test('uses complete SUMO-road routes without straight-line fallbacks', () => {
  const nodes = parseIntersectionTopologyCatalog(catalog)
  const links = buildIntersectionTopologyLinks(nodes)
  const routes = parseIntersectionTopologyRoutes(routeManifest, catalog.sourceSha256)
  assert.deepEqual(
    routes.routes.map((route) => route.routeId).sort(),
    links.map((link) => link.id).sort(),
  )
  const nodesById = new Map(nodes.map((node) => [node.intersectionId, node]))
  for (const route of routes.routes) {
    assert.ok(route.coordinates.length > 2, route.routeId)
    const start = route.coordinates[0]
    const end = route.coordinates.at(-1)
    assert.ok(topologyDistanceMeters(nodesById.get(route.from), {
      longitude: start[0],
      latitude: start[1],
    }) < 35, `${route.routeId} start`)
    assert.ok(topologyDistanceMeters(nodesById.get(route.to), {
      longitude: end[0],
      latitude: end[1],
    }) < 35, `${route.routeId} end`)
  }
})

test('builds one padded navigation bound that contains every demo', () => {
  const nodes = parseIntersectionTopologyCatalog(catalog)
  const bounds = intersectionTopologyBounds(nodes)

  assert.ok(bounds)
  const [west, south, east, north] = bounds
  assert.ok(nodes.every((node) => (
    node.longitude > west
    && node.longitude < east
    && node.latitude > south
    && node.latitude < north
  )))
})

test('computes a viewport-aware range that can frame all twenty intersections', () => {
  const nodes = parseIntersectionTopologyCatalog(catalog)
  const desktopRange = intersectionTopologyMaxRange(nodes, 16 / 9)
  const narrowRange = intersectionTopologyMaxRange(nodes, 3 / 4)

  assert.ok(desktopRange >= 35_000 && desktopRange <= 48_000)
  assert.ok(narrowRange >= desktopRange)
  assert.ok(narrowRange <= 48_000)
})
