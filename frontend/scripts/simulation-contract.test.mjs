import assert from 'node:assert/strict'
import test from 'node:test'

import {
  catalogSupportsIntersection,
  catalogSupportsScenarioPreset,
  findCatalogIntersection,
  missingPresetIntersectionIds,
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
  scenario_presets: [{
    preset_id: 'xiongan_20',
    label: '雄安20路口路网',
    intersection_ids: ['demo_1', 'demo_2'],
    map_template: 'xiongan20',
  }],
  event_types: [],
  control_modes: ['fixed'],
  playback_speeds: [1, 2],
}

test('catalog lookup never falls back to another intersection', () => {
  assert.equal(findCatalogIntersection(catalog, 'demo_2'), demo2)
  assert.equal(findCatalogIntersection(catalog, 'demo_8'), null)
  assert.equal(catalogSupportsIntersection(catalog, 'demo_8'), false)
})

test('simulation contract requires every preset intersection artifact', () => {
  assert.deepEqual(missingPresetIntersectionIds(catalog, 'xiongan_20'), ['demo_1'])
  assert.equal(catalogSupportsScenarioPreset(catalog, 'xiongan_20'), false)
})

test('simulation contract requires an explicitly supported intersection', () => {
  assert.throws(
    () => requireSimulatableIntersection(null),
    /仅支持高精度查看/,
  )
  assert.equal(requireSimulatableIntersection(demo2), demo2)
})
