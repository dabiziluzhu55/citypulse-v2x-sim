import assert from 'node:assert/strict'
import test from 'node:test'

import {
  catalogSupportsIntersection,
  findCatalogIntersection,
  requireSimulatableIntersection,
} from '../src/composables/catalogCapabilities.ts'

const demo2 = {
  intersection_id: 'demo_2',
  longitude: 116.5,
  latitude: 39.8,
  periods: ['morning_peak'],
  origins: [],
  lanes: [],
}

const catalog = {
  intersections: [demo2],
  event_types: [],
  control_modes: ['fixed'],
  flow_multiplier: { min: 0.1, max: 5 },
}

test('catalog lookup never falls back to another intersection', () => {
  assert.equal(findCatalogIntersection(catalog, 'demo_2'), demo2)
  assert.equal(findCatalogIntersection(catalog, 'demo_8'), null)
  assert.equal(catalogSupportsIntersection(catalog, 'demo_8'), false)
})

test('simulation contract requires an explicitly supported intersection', () => {
  assert.throws(
    () => requireSimulatableIntersection(null),
    /仅支持高精度查看/,
  )
  assert.equal(requireSimulatableIntersection(demo2), demo2)
})
