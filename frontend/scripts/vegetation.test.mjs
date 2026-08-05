import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildVegetationGround,
  buildVegetationManifest,
} from './generate-scene-vegetation.mjs'
import { parseSceneVegetationManifest } from '../src/mapv/showcaseLayers/sceneVegetation.ts'

const roads = JSON.parse(await readFile(
  new URL('../public/showcase-data/demo_2.roads.wgs84.geojson', import.meta.url),
  'utf8',
))
const facilities = JSON.parse(await readFile(
  new URL('../public/showcase-data/demo_2.facilities.json', import.meta.url),
  'utf8',
))

test('generates deterministic vegetation outside road surfaces', () => {
  const first = buildVegetationManifest(roads, facilities)
  const second = buildVegetationManifest(roads, facilities)

  assert.deepEqual(first, second)
  assert.ok(first.items.length >= 100)
  assert.ok(new Set(first.items.map((item) => item.cell)).size > 1)
  assert.ok(first.items.some((item) => item.kind === 'tree'))
  assert.ok(first.items.some((item) => item.kind === 'bush'))
  assert.equal(new Set(first.items.map((item) => item.id)).size, first.items.length)
  assert.equal(parseSceneVegetationManifest(first).items.length, first.items.length)
  const ground = buildVegetationGround(first, roads)
  assert.ok(ground.features.length > 20)
  assert.ok(ground.features.every((feature) => feature.geometry.type === 'Polygon'))
})

test('rejects malformed vegetation manifests', () => {
  assert.throws(
    () => parseSceneVegetationManifest({ schemaVersion: 1, cellSizeMeters: 0, items: [] }),
    /cellSizeMeters/,
  )
})
