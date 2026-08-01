import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildIntersectionTopologyLinks,
  intersectionTopologyBounds,
  intersectionTopologyMaxRange,
  parseIntersectionTopologyCatalog,
} from '../src/mapv/intersectionTopology.ts'

const catalog = JSON.parse(await readFile(
  new URL('../public/intersections/v3/catalog.json', import.meta.url),
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
