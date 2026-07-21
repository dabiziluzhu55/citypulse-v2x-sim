import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  createBuildingProjector,
  parseGlb,
  reprojectGlb,
} from './reproject-building-tileset.mjs'

const tileset = JSON.parse(readFileSync(
  new URL('../public/3dtiles/xiongan/tileset.json', import.meta.url),
  'utf8',
))
const sampleGlb = readFileSync(
  new URL('../public/3dtiles/xiongan/tiles/tile_facade_0.glb', import.meta.url),
)

test('building projection maps the ECEF root to a local BD-09 WebMercator origin', () => {
  const project = createBuildingProjector(tileset.root.transform, 'wgs84')
  const origin = project([0, 0, 0])
  const east = project([1_000, 0, 0])

  assert.ok(Math.hypot(origin[0], origin[1], origin[2]) < 1e-6)
  assert.ok(east[0] > 1_270 && east[0] < 1_300)
  assert.ok(Math.abs(east[1]) < 25)
  assert.ok(Math.abs(east[2]) < 1)
})

test('GLB reprojection updates positions and bounds without changing mesh contracts', () => {
  const before = parseGlb(sampleGlb)
  const result = reprojectGlb(sampleGlb, ([x, y, z]) => [x * 2, y * 3, z + 5])
  const after = parseGlb(result.glb)
  const beforePosition = before.json.accessors[1]
  const afterPosition = after.json.accessors[1]

  assert.equal(result.vertexCount, beforePosition.count)
  assert.deepEqual(after.json.meshes, before.json.meshes)
  assert.deepEqual(after.json.materials, before.json.materials)
  assert.equal(afterPosition.count, beforePosition.count)
  assert.deepEqual(afterPosition.min, [
    Math.fround(beforePosition.min[0] * 2),
    Math.fround(beforePosition.min[1] * 3),
    Math.fround(beforePosition.min[2] + 5),
  ])
  assert.deepEqual(afterPosition.max, [
    Math.fround(beforePosition.max[0] * 2),
    Math.fround(beforePosition.max[1] * 3),
    Math.fround(beforePosition.max[2] + 5),
  ])
  assert.deepEqual(result.bounds.min, afterPosition.min)
  assert.deepEqual(result.bounds.max, afterPosition.max)
})
