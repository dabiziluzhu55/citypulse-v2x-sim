import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { LatheGeometry, Mesh } from 'three'

import {
  INTERSECTION_MARKER_EFFECT_OPTIONS,
  INTERSECTION_MARKER_LABEL_MAX_RANGE_METERS,
  INTERSECTION_MARKER_MODEL_URL,
  INTERSECTION_MARKER_ROTATION_PERIOD_MS,
  INTERSECTION_MARKER_SIZE_METERS,
  INTERSECTION_MARKER_WAVE_OPTIONS,
  createFallbackIntersectionMarkerModel,
  markerWorldProjectionRatio,
  partitionIntersectionMarkerFeatures,
  shouldShowIntersectionMarkerLabel,
} from '../src/mapv/intersectionMarkerStyle.ts'

test('vendors the exact Yizhuang marker model with local runtime loading', async () => {
  const model = await readFile(new URL('../public/models/yizhuang_cross_1.glb', import.meta.url))
  assert.equal(INTERSECTION_MARKER_MODEL_URL, '/models/yizhuang_cross_1.glb')
  assert.equal(model.length, 6_888)
  assert.equal(
    createHash('sha256').update(model).digest('hex'),
    '876f2ff9e8a196d664366118e94dbe7dbeccdb16238e5bb1d6ada7df9a1fe9b4',
  )
})

test('uses one thirty-metre world-size marker contract for normal and active states', () => {
  assert.equal(INTERSECTION_MARKER_SIZE_METERS, 30)
  assert.equal(INTERSECTION_MARKER_ROTATION_PERIOD_MS, 8_000)
  assert.equal(INTERSECTION_MARKER_EFFECT_OPTIONS.keepSize, false)
  assert.equal(INTERSECTION_MARKER_EFFECT_OPTIONS.size, 30)
  assert.equal(INTERSECTION_MARKER_EFFECT_OPTIONS.animationJump, false)
  assert.equal(INTERSECTION_MARKER_EFFECT_OPTIONS.animationRotate, true)
  assert.equal(INTERSECTION_MARKER_EFFECT_OPTIONS.animationRotatePeriod, 8_000)
  assert.equal(INTERSECTION_MARKER_WAVE_OPTIONS.keepSize, false)
})

test('normal and selected marker sources are mutually exclusive', () => {
  const features = Array.from({ length: 20 }, (_, index) => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [index, index, 0] },
    properties: { intersection_id: `demo_${index + 1}` },
  }))
  const partition = partitionIntersectionMarkerFeatures(features, 'demo_7')
  assert.equal(partition.normal.length, 19)
  assert.equal(partition.active.length, 1)
  assert.equal(partition.active[0].properties.intersection_id, 'demo_7')
  assert.ok(!partition.normal.some((feature) => feature.properties.intersection_id === 'demo_7'))
})

test('world-size marker projection changes monotonically with camera range', () => {
  const near = markerWorldProjectionRatio(250)
  const middle = markerWorldProjectionRatio(3_000)
  const overview = markerWorldProjectionRatio(40_000)
  assert.ok(near > middle)
  assert.ok(middle > overview)
  assert.equal(near / overview, 160)
})

test('selected labels disappear outside the readable near-range window', () => {
  assert.equal(shouldShowIntersectionMarkerLabel(250, true), true)
  assert.equal(shouldShowIntersectionMarkerLabel(INTERSECTION_MARKER_LABEL_MAX_RANGE_METERS, true), true)
  assert.equal(shouldShowIntersectionMarkerLabel(INTERSECTION_MARKER_LABEL_MAX_RANGE_METERS + 1, true), false)
  assert.equal(shouldShowIntersectionMarkerLabel(250, false), false)
})

test('model failure fallback is a lightweight teardrop and preserves world scaling', () => {
  const fallback = createFallbackIntersectionMarkerModel(false)
  const meshes = []
  fallback.traverse((child) => {
    if (child instanceof Mesh) meshes.push(child)
  })
  assert.equal(meshes.length, 1)
  assert.ok(meshes[0].geometry instanceof LatheGeometry)
  assert.match(fallback.name, /teardrop/)
  meshes[0].geometry.dispose()
  meshes[0].material.dispose()
})
