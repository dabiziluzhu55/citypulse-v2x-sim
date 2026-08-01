import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_PLAYBACK_SPEED_OPTIONS,
  DISTURBANCE_EVENT_OPTIONS,
  resolveCatalogEventTypes,
  resolveCatalogPlaybackSpeeds,
  SIMULATION_TIME_OPTIONS,
  simulationTimeWindow,
} from '../src/constants/scenarioOptions.ts'
import { formatIntersectionLabel } from '../src/utils/intersectionLabels.ts'
import { buildStartSimulationRequest } from '../src/utils/scenarioPayload.ts'
import { buildDisturbanceWarningAggregates } from '../src/utils/disturbanceWarnings.ts'
import {
  SCENARIO_CONFIG_EXPORT_VERSION,
  resolveImportedDisturbanceTimes,
} from '../src/utils/scenarioConfigMigration.ts'
import {
  catalogSupportsScenarioPreset,
  catalogSupportsScenarioPresetForIntersection,
  findRunnableScenarioPreset,
  missingPresetIntersectionIds,
} from '../src/composables/catalogCapabilities.ts'
import {
  SUPPORTED_BACKEND_CONTROL_MODES,
  resolveCatalogControlModes,
} from '../src/constants/simulationOptions.ts'

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
  assert.equal(formatIntersectionLabel('demo_2'), '路口2')
  assert.equal(formatIntersectionLabel('demo_20'), '路口20')
  assert.equal(formatIntersectionLabel('custom'), 'custom')
})

test('exposes exactly five disturbance presets backed by supported event types', () => {
  assert.deepEqual(
    DISTURBANCE_EVENT_OPTIONS.map((item) => item.label),
    ['施工占道', '道路限速', '大型活动散场', '大型活动开场', '交通事故'],
  )
  assert.ok(DISTURBANCE_EVENT_OPTIONS.every((item) => (
    ['lane_closure', 'speed_limit', 'accident'].includes(item.eventType)
  )))
})

test('uses the main backend catalog contract while the catalog is offline', () => {
  assert.deepEqual(resolveCatalogControlModes(null), [...SUPPORTED_BACKEND_CONTROL_MODES])
  assert.deepEqual(resolveCatalogPlaybackSpeeds(undefined), [...DEFAULT_PLAYBACK_SPEED_OPTIONS])
  assert.deepEqual(resolveCatalogEventTypes(null), ['lane_closure', 'speed_limit', 'accident'])
  assert.deepEqual(resolveCatalogControlModes(['fixed']), ['fixed'])
  assert.deepEqual(resolveCatalogPlaybackSpeeds([1, 2]), [1, 2])
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
  assert.deepEqual(Object.keys(payload).sort(), [
    'control_mode',
    'disturbance_targets',
    'duration_seconds',
    'gui',
    'origins',
    'period',
    'playback_speed',
    'realtime',
    'scenario_preset_id',
    'seed',
    'snapshot_interval_seconds',
    'step_length',
    'window_start_seconds',
  ])
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

test('flattens multiple configured events into unique backend disturbance targets', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'east_dense',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 600,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [
      { eventId: 'construction', eventType: 'lane_closure', intersectionIds: ['demo_1', 'demo_2'] },
      { eventId: 'arrival', eventType: 'speed_limit', intersectionIds: ['demo_2'] },
      { eventId: 'incident', eventType: 'accident', intersectionIds: ['demo_3'] },
    ],
    snapshotIntervalSeconds: 0.2,
  })
  assert.deepEqual(
    payload.disturbance_targets.map((target) => target.event_type),
    ['lane_closure', 'lane_closure', 'speed_limit', 'accident'],
  )
  assert.deepEqual(
    payload.disturbance_targets.map((target) => target.intersection_id),
    ['demo_1', 'demo_2', 'demo_2', 'demo_3'],
  )
  assert.equal(new Set(payload.disturbance_targets.map((target) => target.event_id)).size, 4)
})

test('uses each configured event time and rejects an event outside the simulation window', () => {
  const input = {
    scenarioPresetId: 'xiongan_20',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 900,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [{
      eventId: 'timed',
      eventType: 'accident',
      intersectionIds: ['demo_1'],
      startSeconds: 120,
      endSeconds: 480,
    }],
    snapshotIntervalSeconds: 0.2,
  }
  const payload = buildStartSimulationRequest(input)
  assert.equal(payload.disturbance_targets[0].start_seconds, 120)
  assert.equal(payload.disturbance_targets[0].end_seconds, 480)
  assert.throws(() => buildStartSimulationRequest({
    ...input,
    disturbanceEvents: [{ ...input.disturbanceEvents[0], endSeconds: 901 }],
  }), /simulation window/)
})

test('aggregates multiple warnings per intersection and tracks active/completed state', () => {
  const nodes = [
    { intersectionId: 'demo_1', longitude: 116.1, latitude: 39 },
    { intersectionId: 'demo_2', longitude: 116.2, latitude: 39.1 },
  ]
  const events = [
    {
      event_id: 'one', preset_id: 'construction', event_type: 'lane_closure',
      intersection_ids: ['demo_1'], start_time: '07:10', end_time: '07:20',
    },
    {
      event_id: 'two', preset_id: 'accident', event_type: 'accident',
      intersection_ids: ['demo_1', 'demo_2'], start_time: '07:15', end_time: '07:30',
    },
  ]
  const active = buildDisturbanceWarningAggregates(nodes, events, '07:00', 16 * 60)
  assert.equal(active.find((item) => item.intersectionId === 'demo_1').events.length, 2)
  assert.equal(active.find((item) => item.intersectionId === 'demo_1').status, 'active')
  const completed = buildDisturbanceWarningAggregates(nodes, events, '07:00', 31 * 60)
  assert.ok(completed.every((item) => item.status === 'completed'))
})

test('migrates v4 events to the outer window and exports v5 scene configuration', () => {
  assert.deepEqual(resolveImportedDisturbanceTimes({}, '07:10', '07:40'), {
    startTime: '07:10',
    endTime: '07:40',
  })
  assert.deepEqual(resolveImportedDisturbanceTimes({
    start_time: '07:15', end_time: '07:25',
  }, '07:10', '07:40'), {
    startTime: '07:15',
    endTime: '07:25',
  })
  assert.equal(SCENARIO_CONFIG_EXPORT_VERSION, 5)
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
