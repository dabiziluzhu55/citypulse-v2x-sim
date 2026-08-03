import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildRoadTransitionSections,
  roadBoundaryFadeFlags,
} from '../src/mapv/realistic/roadTransition.ts'

test('fades only endpoints clipped by the intersection patch radius', () => {
  assert.deepEqual(roadBoundaryFadeFlags([[0, 0], [99.6, 0]], 100), {
    fadeStart: false,
    fadeEnd: true,
  })
  assert.deepEqual(roadBoundaryFadeFlags([[-100, 0], [0, 0], [99.7, 0]], 100), {
    fadeStart: true,
    fadeEnd: true,
  })
  assert.deepEqual(roadBoundaryFadeFlags([[0, 0], [94, 0]], 100), {
    fadeStart: false,
    fadeEnd: false,
  })
})

test('keeps internal road strips opaque', () => {
  assert.deepEqual(buildRoadTransitionSections([[0, 0], [100, 0]], false, false, 20), [{
    points: [[0, 0], [100, 0]],
    opacity: 1,
  }])
})

test('fades only the road endpoints that reach a patch boundary', () => {
  const start = buildRoadTransitionSections([[0, 0], [100, 0]], true, false, 20)
  assert.equal(start.length, 4)
  assert.ok(start[0].opacity < start[1].opacity)
  assert.equal(start.at(-1).opacity, 1)
  assert.deepEqual(start[0].points[0], [0, 0])
  assert.deepEqual(start.at(-1).points.at(-1), [100, 0])

  const both = buildRoadTransitionSections([[0, 0], [100, 0]], true, true, 20)
  assert.ok(both[0].opacity < 0.3)
  assert.ok(both.at(-1).opacity < 0.3)
  assert.ok(both.some((item) => item.opacity === 1))
})

test('caps transition length for short road fragments', () => {
  const sections = buildRoadTransitionSections([[0, 0], [8, 0]], true, false, 20)
  assert.deepEqual(sections.at(-1).points.at(-1), [8, 0])
  assert.ok(sections.every((item) => item.points.length >= 2))
})
