import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SIMULATION_TIME_OPTIONS,
  simulationTimeWindow,
} from '../src/constants/scenarioOptions.ts'
import { formatIntersectionLabel } from '../src/utils/intersectionLabels.ts'
import { buildStartSimulationRequest } from '../src/utils/scenarioPayload.ts'
import {
  catalogSupportsScenarioPreset,
  catalogSupportsScenarioPresetForIntersection,
  findRunnableScenarioPreset,
  missingPresetIntersectionIds,
} from '../src/composables/catalogCapabilities.ts'

const EXPECTED_LABELS = {
  morning_peak: [
    '7:00-7:15', '7:15-7:30', '7:30-7:45', '7:45-8:00',
    '8:00-8:15', '8:15-8:30', '8:30-8:45', '8:45-9:00',
  ],
  flat: [
    '14:30-14:45', '14:45-15:00', '15:00-15:15', '15:15-15:30',
    '15:30-15:45', '15:45-16:00', '16:00-16:15', '16:15-16:30',
  ],
  evening_peak: [
    '17:30-17:45', '17:45-18:00', '18:00-18:15', '18:15-18:30',
    '18:30-18:45', '18:45-19:00', '19:00-19:15', '19:15-19:30',
  ],
}

test('provides eight exact 15-minute windows for each official period', () => {
  assert.equal(SIMULATION_TIME_OPTIONS.length, 24)
  for (const [flowMode, labels] of Object.entries(EXPECTED_LABELS)) {
    const options = SIMULATION_TIME_OPTIONS.filter((item) => item.flowMode === flowMode)
    assert.deepEqual(options.map((item) => item.label), labels)
    assert.deepEqual(options.map((item) => item.windowStartSeconds), [0, 900, 1800, 2700, 3600, 4500, 5400, 6300])
    assert.ok(options.every((item) => item.durationSeconds === 900))
  }
})

test('keeps the final evening window aligned to the backend offset contract', () => {
  const option = SIMULATION_TIME_OPTIONS.find((item) => item.value === 'evening_1900')
  assert.ok(option)
  assert.equal(option.flowMode, 'evening_peak')
  assert.equal(option.windowStartSeconds, 5400)
  assert.equal(option.durationSeconds, 900)
})

test('converts arbitrary clock ranges inside each official period', () => {
  assert.deepEqual(
    simulationTimeWindow('morning_peak', '07:20', '08:35'),
    { windowStartSeconds: 1200, durationSeconds: 4500 },
  )
  assert.deepEqual(
    simulationTimeWindow('flat', '14:30', '16:30'),
    { windowStartSeconds: 0, durationSeconds: 7200 },
  )
  assert.throws(() => simulationTimeWindow('evening_peak', '17:00', '18:00'))
  assert.throws(() => simulationTimeWindow('evening_peak', '19:00', '18:00'))
})

test('formats demo labels without changing backend ids', () => {
  assert.equal(formatIntersectionLabel('demo_2'), 'demo2')
  assert.equal(formatIntersectionLabel('demo_20'), 'demo20')
  assert.equal(formatIntersectionLabel('custom'), 'custom')
})

test('builds the backend v2 preset request without removed legacy fields', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'xiongan_20',
    period: 'morning_peak',
    windowStartSeconds: 900,
    durationSeconds: 900,
    controlMode: 'sotl',
    playbackSpeed: 1.5,
    disturbance: 'lane_closure',
    intersectionId: 'demo_2',
    eventId: 'evt-test',
    snapshotIntervalSeconds: 0.2,
  })

  assert.equal(payload.scenario_preset_id, 'xiongan_20')
  assert.equal(payload.period, 'morning_peak')
  assert.equal(payload.window_start_seconds, 900)
  assert.equal(payload.duration_seconds, 900)
  assert.equal(payload.control_mode, 'sotl')
  assert.equal(payload.playback_speed, 1.5)
  assert.equal(payload.disturbance_targets[0].intersection_id, 'demo_2')
  assert.equal('intersection_ids' in payload, false)
  assert.equal('flow_multiplier' in payload, false)
  assert.equal('initial_events' in payload, false)
})

test('builds one unique disturbance target for every selected intersection', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'east_dense',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 600,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbance: 'accident',
    disturbanceIntersectionIds: ['demo_3', 'demo_5', 'demo_3'],
    eventId: 'evt-test',
    snapshotIntervalSeconds: 0.2,
  })
  assert.deepEqual(
    payload.disturbance_targets.map((target) => target.intersection_id),
    ['demo_3', 'demo_5'],
  )
  assert.equal(new Set(payload.disturbance_targets.map((target) => target.event_id)).size, 2)
})

test('marks a scenario unavailable until every preset intersection is in the catalog', () => {
  const catalog = {
    intersections: [{ intersection_id: 'demo_2' }],
    scenario_presets: [{
      preset_id: 'xiongan_20',
      label: '雄安20路口路网',
      intersection_ids: ['demo_1', 'demo_2', 'demo_3'],
      map_template: 'xiongan20',
    }],
    event_types: [],
    control_modes: ['fixed'],
    playback_speeds: [1],
  }

  assert.deepEqual(missingPresetIntersectionIds(catalog, 'xiongan_20'), ['demo_1', 'demo_3'])
  assert.equal(catalogSupportsScenarioPreset(catalog, 'xiongan_20'), false)
  catalog.intersections.push({ intersection_id: 'demo_1' }, { intersection_id: 'demo_3' })
  assert.equal(catalogSupportsScenarioPreset(catalog, 'xiongan_20'), true)
})

test('selects the complete single-intersection preset for demo_2', () => {
  const catalog = {
    intersections: [{ intersection_id: 'demo_2' }],
    scenario_presets: [
      {
        preset_id: 'xiongan_20',
        label: '雄安20路口路网',
        intersection_ids: ['demo_1', 'demo_2', 'demo_3'],
        map_template: 'xiongan20',
      },
      {
        preset_id: 'demo_2_single',
        label: 'demo_2 单路口真实仿真',
        intersection_ids: ['demo_2'],
        map_template: 'xiongan20',
      },
    ],
    event_types: [],
    control_modes: ['fixed'],
    playback_speeds: [1],
  }

  assert.equal(catalogSupportsScenarioPreset(catalog, 'xiongan_20'), false)
  assert.equal(
    catalogSupportsScenarioPresetForIntersection(catalog, 'demo_2_single', 'demo_2'),
    true,
  )
  assert.equal(findRunnableScenarioPreset(catalog, 'demo_2')?.preset_id, 'demo_2_single')
  assert.equal(findRunnableScenarioPreset(catalog, 'demo_3'), null)
})
