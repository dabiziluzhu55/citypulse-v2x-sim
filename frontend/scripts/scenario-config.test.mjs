import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_PLAYBACK_SPEED_OPTIONS,
  DISTURBANCE_EVENT_OPTIONS,
  SCENARIO_MODE_OPTIONS,
  resolveCatalogEventTypes,
  resolveCatalogPlaybackSpeeds,
  SIMULATION_TIME_OPTIONS,
  clampClockTime,
  clockPartOptions,
  disabledClockHours,
  disabledClockMinutes,
  maximumSimulationEndTime,
  stepClockHour,
  stepClockMinute,
  simulationEndClockValues,
  simulationStartClockValues,
  simulationTimeWindow,
} from '../src/constants/scenarioOptions.ts'
import { formatIntersectionLabel } from '../src/utils/intersectionLabels.ts'
import { buildStartSimulationRequest } from '../src/utils/scenarioPayload.ts'
import {
  assertSafeLaneClosureEvents,
  laneClosureAvailability,
} from '../src/utils/safeLaneClosures.ts'
import {
  assertUniqueDisturbanceIntersections,
  disturbanceIntersectionOwners,
} from '../src/utils/disturbanceIntersectionUniqueness.ts'
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
  DASHBOARD_CONTROL_MODES,
  controlModePeriodCompatibility,
  requirePeriodCompatibleControlMode,
  resolveCatalogControlModes,
} from '../src/constants/simulationOptions.ts'
import {
  MAX_MAJOR_EVENT_VEHICLE_COUNT,
  MIN_MAJOR_EVENT_VEHICLE_COUNT,
  resolveMajorEventVehicleCount,
} from '../src/utils/scenarioConfigMigration.ts'
import {
  controlModeSupportsScenario,
  disturbanceTargetsOutsideScenario,
  reconcileEventsForScenario,
  scenarioPresetIntersectionIds,
} from '../src/utils/scenarioPresetRules.ts'

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
    simulationTimeWindow('morning_peak', '07:20', '07:35'),
    { windowStartSeconds: 1200, durationSeconds: 900 },
  )
  assert.deepEqual(
    simulationTimeWindow('flat', '14:30', '14:45'),
    { windowStartSeconds: 0, durationSeconds: 900 },
  )
  assert.equal(maximumSimulationEndTime('morning_peak', '08:55'), '09:00')
  assert.throws(() => simulationTimeWindow('morning_peak', '07:20', '07:36'))
  assert.throws(() => simulationTimeWindow('evening_peak', '17:00', '18:00'))
  assert.throws(() => simulationTimeWindow('evening_peak', '19:00', '18:00'))
})

test('builds hour-first minute options inside all official periods', () => {
  assert.deepEqual(
    [simulationStartClockValues('morning_peak')[0], simulationStartClockValues('morning_peak').at(-1)],
    ['07:00', '08:59'],
  )
  assert.deepEqual(
    [simulationStartClockValues('flat')[0], simulationStartClockValues('flat').at(-1)],
    ['14:30', '16:29'],
  )
  assert.deepEqual(
    [simulationStartClockValues('evening_peak')[0], simulationStartClockValues('evening_peak').at(-1)],
    ['17:30', '19:29'],
  )
  const endValues = simulationEndClockValues('morning_peak', '08:55')
  assert.deepEqual(endValues, ['08:56', '08:57', '08:58', '08:59', '09:00'])
  assert.deepEqual(clockPartOptions(endValues), {
    hours: ['08', '09'],
    minutesByHour: { '08': ['56', '57', '58', '59'], '09': ['00'] },
  })
  assert.equal(simulationEndClockValues('flat', '14:59').length, 15)
  assert.equal(simulationEndClockValues('flat', '14:59').at(-1), '15:14')
})

test('builds disabled hour and minute sets for the two time pickers', () => {
  const morningStarts = simulationStartClockValues('morning_peak')
  assert.equal(disabledClockHours(morningStarts).includes(7), false)
  assert.equal(disabledClockHours(morningStarts).includes(8), false)
  assert.equal(disabledClockHours(morningStarts).includes(9), true)

  const endingAtNine = simulationEndClockValues('morning_peak', '08:55')
  assert.deepEqual(disabledClockHours(endingAtNine).filter((hour) => hour === 8 || hour === 9), [])
  assert.equal(disabledClockMinutes(endingAtNine, 8).includes(55), true)
  assert.equal(disabledClockMinutes(endingAtNine, 8).includes(56), false)
  assert.equal(disabledClockMinutes(endingAtNine, 9).includes(0), false)
  assert.equal(disabledClockMinutes(endingAtNine, 9).includes(1), true)
})

test('steps hours and minutes immediately while clamping to the active window', () => {
  assert.equal(stepClockMinute('14:59', 1, '14:30', '15:15'), '15:00')
  assert.equal(stepClockMinute('15:00', -1, '14:30', '15:15'), '14:59')
  assert.equal(stepClockHour('14:45', 1, '14:30', '16:29'), '15:45')
  assert.equal(stepClockHour('15:45', -1, '14:30', '16:29'), '14:45')
  assert.equal(stepClockHour('15:45', 1, '14:30', '16:05'), '16:05')
  assert.equal(stepClockMinute('14:30', -1, '14:30', '14:45'), '14:30')
  assert.equal(stepClockMinute('14:45', 1, '14:30', '14:45'), '14:45')
  assert.equal(clampClockTime('17:00', '17:30', '19:29'), '17:30')
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
    ['lane_closure', 'speed_limit', 'accident', 'major_event_opening', 'major_event_closing'].includes(item.eventType)
  )))
})

test('uses the main backend catalog contract while the catalog is offline', () => {
  assert.deepEqual(resolveCatalogControlModes(null), [...SUPPORTED_BACKEND_CONTROL_MODES])
  assert.deepEqual(resolveCatalogPlaybackSpeeds(undefined), [...DEFAULT_PLAYBACK_SPEED_OPTIONS])
  assert.deepEqual(resolveCatalogEventTypes(null), [
    'lane_closure', 'speed_limit', 'accident', 'major_event_opening', 'major_event_closing',
  ])
  assert.deepEqual(resolveCatalogControlModes(['fixed']), ['fixed'])
  assert.deepEqual(resolveCatalogPlaybackSpeeds([1, 2]), [1, 2])
})

test('exposes all five control modes from the latest backend contract', () => {
  assert.deepEqual(DASHBOARD_CONTROL_MODES.map((item) => item.value), [
    'fixed', 'max_pressure', 'sotl', 'ippo', 'mappo',
  ])
  assert.equal(DASHBOARD_CONTROL_MODES.find((item) => item.value === 'mappo').backendSupported, true)
  assert.equal(SUPPORTED_BACKEND_CONTROL_MODES.includes('mappo'), true)
})

test('requires major-event vehicle counts between twenty and two hundred', () => {
  assert.equal(MIN_MAJOR_EVENT_VEHICLE_COUNT, 20)
  assert.equal(MAX_MAJOR_EVENT_VEHICLE_COUNT, 200)
  assert.equal(resolveMajorEventVehicleCount(undefined), 20)
  assert.equal(resolveMajorEventVehicleCount(20), 20)
  assert.equal(resolveMajorEventVehicleCount(200), 200)
  assert.throws(() => resolveMajorEventVehicleCount(19), /20-200/)
  assert.throws(() => resolveMajorEventVehicleCount(201), /20-200/)
  assert.throws(() => resolveMajorEventVehicleCount(20.5), /20-200/)
})

test('uses exact disturbance intersections for all three scene presets', () => {
  assert.deepEqual(SCENARIO_MODE_OPTIONS.map((item) => item.value), [
    'xiongan_20', 'east_dense', 'west_dense',
  ])
  assert.deepEqual(scenarioPresetIntersectionIds('xiongan_20'), Array.from(
    { length: 20 }, (_, index) => `demo_${index + 1}`,
  ))
  assert.deepEqual(scenarioPresetIntersectionIds('east_dense'), ['demo_3', 'demo_5', 'demo_6', 'demo_9'])
  assert.deepEqual(scenarioPresetIntersectionIds('west_dense'), ['demo_14', 'demo_15', 'demo_19'])
  assert.deepEqual(scenarioPresetIntersectionIds('east_dense', [{
    preset_id: 'east_dense',
    intersection_ids: ['demo_2'],
  }]), ['demo_3', 'demo_5', 'demo_6', 'demo_9'])
})

test('reconciles configured events when switching to a smaller scene', () => {
  const reconciliation = reconcileEventsForScenario([
    { event_id: 'mixed', intersection_ids: ['demo_3', 'demo_14'] },
    { event_id: 'removed', intersection_ids: ['demo_2'] },
  ], scenarioPresetIntersectionIds('east_dense'))
  assert.deepEqual(reconciliation.removedIntersectionIds, ['demo_2', 'demo_14'])
  assert.equal(reconciliation.removedEventCount, 1)
  assert.deepEqual(reconciliation.events, [{ event_id: 'mixed', intersection_ids: ['demo_3'] }])
})

test('validates disturbance targets against the selected scene instead of the incomplete catalog', () => {
  const events = [{ intersection_ids: ['demo_3', 'demo_5'] }]
  assert.deepEqual(disturbanceTargetsOutsideScenario(
    events,
    scenarioPresetIntersectionIds('east_dense'),
  ), [])
  assert.deepEqual(disturbanceTargetsOutsideScenario(
    [{ intersection_ids: ['demo_2', 'demo_3'] }],
    scenarioPresetIntersectionIds('east_dense'),
  ), ['demo_2'])
})

test('allows IPPO and MAPPO in all three backend scene presets', () => {
  assert.equal(controlModeSupportsScenario('ippo', 'xiongan_20'), true)
  assert.equal(controlModeSupportsScenario('ippo', 'east_dense'), true)
  assert.equal(controlModeSupportsScenario('mappo', 'west_dense'), true)
  assert.equal(controlModeSupportsScenario('fixed', 'east_dense'), true)
  assert.equal(controlModeSupportsScenario('unknown', 'east_dense'), false)
})

test('limits the current IPPO checkpoint to off-peak without restricting MAPPO', () => {
  assert.equal(controlModePeriodCompatibility('ippo', 'off_peak').compatible, true)
  assert.equal(controlModePeriodCompatibility('ippo', 'morning_peak').compatible, false)
  assert.equal(controlModePeriodCompatibility('ippo', 'evening_peak').compatible, false)
  assert.equal(controlModePeriodCompatibility('mappo', 'morning_peak').compatible, true)
  assert.throws(
    () => requirePeriodCompatibleControlMode('ippo', 'morning_peak'),
    /IPPO.*平峰/,
  )
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
  assert.equal(payload.step_length, 0.1)
  assert.equal(payload.snapshot_interval_seconds, 0.2)
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

test('submits MAPPO without remapping it to another control mode', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'west_dense',
    period: 'off_peak',
    windowStartSeconds: 0,
    durationSeconds: 900,
    controlMode: 'mappo',
    playbackSpeed: 5,
    disturbanceEvents: [],
    snapshotIntervalSeconds: 0.2,
  })
  assert.equal(payload.control_mode, 'mappo')
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

test('converts speed limit from km/h to m/s and rejects values outside 20-80', () => {
  const baseInput = {
    scenarioPresetId: 'east_dense',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 600,
    controlMode: 'fixed',
    playbackSpeed: 1,
    snapshotIntervalSeconds: 0.2,
  }

  const payload = buildStartSimulationRequest({
    ...baseInput,
    disturbanceEvents: [{
      eventType: 'speed_limit',
      intersectionIds: ['demo_6'],
      speedLimitKmh: 60,
    }],
  })

  assert.equal(payload.disturbance_targets[0].max_speed, 60 / 3.6)

  for (const speedLimitKmh of [19, 81]) {
    assert.throws(() => buildStartSimulationRequest({
      ...baseInput,
      disturbanceEvents: [{
        eventType: 'speed_limit',
        intersectionIds: ['demo_6'],
        speedLimitKmh,
      }],
    }), /20-80/)
  }
})

test('flattens multiple non-conflicting events into unique backend disturbance targets', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'east_dense',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 600,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [
      { eventId: 'construction', eventType: 'lane_closure', intersectionIds: ['demo_3', 'demo_5'] },
      { eventId: 'arrival', eventType: 'speed_limit', intersectionIds: ['demo_6'] },
      { eventId: 'incident', eventType: 'accident', intersectionIds: ['demo_9'] },
    ],
    snapshotIntervalSeconds: 0.2,
  })
  assert.deepEqual(
    payload.disturbance_targets.map((target) => target.event_type),
    ['lane_closure', 'lane_closure', 'speed_limit', 'accident'],
  )
  assert.deepEqual(
    payload.disturbance_targets.map((target) => target.intersection_id),
    ['demo_3', 'demo_5', 'demo_6', 'demo_9'],
  )
  assert.equal(new Set(payload.disturbance_targets.map((target) => target.event_id)).size, 4)
})

test('rejects one intersection assigned to multiple events even at different times', () => {
  assert.throws(() => buildStartSimulationRequest({
    scenarioPresetId: 'east_dense',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 600,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [
      {
        eventId: 'construction', eventType: 'lane_closure', intersectionIds: ['demo_5'],
        startOffsetSeconds: 0, endOffsetSeconds: 120,
      },
      {
        eventId: 'arrival', eventType: 'speed_limit', intersectionIds: ['demo_5'],
        startOffsetSeconds: 300, endOffsetSeconds: 420,
      },
    ],
    snapshotIntervalSeconds: 0.2,
  }), /路口5.*lane_closure.*speed_limit/)
})

test('excludes the edited event itself while detecting occupied intersections', () => {
  const events = [
    { event_id: 'first', intersection_ids: ['demo_3', 'demo_5'] },
    { event_id: 'second', intersection_ids: ['demo_6'] },
  ]
  const owners = disturbanceIntersectionOwners(events, 'first')
  assert.equal(owners.has('demo_3'), false)
  assert.equal(owners.has('demo_5'), false)
  assert.equal(owners.get('demo_6')?.event_id, 'second')
  assert.doesNotThrow(() => assertUniqueDisturbanceIntersections(events))
  assert.throws(() => assertUniqueDisturbanceIntersections([
    ...events,
    { event_id: 'imported', intersection_ids: ['demo_6'] },
  ]), /路口6.*second.*imported/)
})

test('uses explicit safe lane ids and rejects construction where no fixed-route-safe lane exists', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'xiongan_20',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 900,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [{
      eventId: 'safe-construction',
      eventType: 'lane_closure',
      intersectionIds: ['demo_1', 'demo_6'],
      startSeconds: 0,
      endSeconds: 600,
    }],
    snapshotIntervalSeconds: 0.2,
  })
  assert.deepEqual(
    payload.disturbance_targets.map((target) => [target.intersection_id, target.lane_ids]),
    [
      ['demo_1', ['-56384_1']],
      ['demo_6', ['-50334_1']],
    ],
  )
  assert.equal(laneClosureAvailability('demo_7').available, false)
  assert.equal(laneClosureAvailability('demo_10').available, false)
  for (const intersectionId of ['demo_7', 'demo_10']) {
    assert.throws(() => assertSafeLaneClosureEvents([{
      event_type: 'lane_closure',
      intersection_ids: [intersectionId],
    }]), new RegExp(`${formatIntersectionLabel(intersectionId)}.*不支持施工占道`))
    assert.throws(() => buildStartSimulationRequest({
      scenarioPresetId: 'xiongan_20',
      period: 'morning_peak',
      windowStartSeconds: 0,
      durationSeconds: 900,
      controlMode: 'fixed',
      playbackSpeed: 1,
      disturbanceEvents: [{ eventType: 'lane_closure', intersectionIds: [intersectionId] }],
      snapshotIntervalSeconds: 0.2,
    }), /不支持施工占道/)
  }
  assert.doesNotThrow(() => assertSafeLaneClosureEvents([{
    event_type: 'speed_limit',
    intersection_ids: ['demo_7', 'demo_10'],
  }]))
})

test('sends real opening and closing events with a per-intersection vehicle count', () => {
  const payload = buildStartSimulationRequest({
    scenarioPresetId: 'xiongan_20',
    period: 'morning_peak',
    windowStartSeconds: 0,
    durationSeconds: 900,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [
      { eventId: 'opening', eventType: 'major_event_opening', intersectionIds: ['demo_1', 'demo_2'], vehicleCount: 30 },
      { eventId: 'closing', eventType: 'major_event_closing', intersectionIds: ['demo_3'] },
    ],
    snapshotIntervalSeconds: 0.2,
  })
  assert.deepEqual(payload.disturbance_targets.map((target) => target.event_type), [
    'major_event_opening', 'major_event_opening', 'major_event_closing',
  ])
  assert.deepEqual(payload.disturbance_targets.map((target) => target.vehicle_count), [30, 30, 20])
  assert.ok(payload.disturbance_targets.every((target) => !('venue_lane_id' in target)))
  assert.throws(() => buildStartSimulationRequest({
    ...payload,
    scenarioPresetId: 'xiongan_20',
    windowStartSeconds: 0,
    durationSeconds: 900,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [{ eventType: 'major_event_opening', intersectionIds: ['demo_1'], vehicleCount: 19 }],
    snapshotIntervalSeconds: 0.2,
  }), /20-200/)
  assert.throws(() => buildStartSimulationRequest({
    ...payload,
    scenarioPresetId: 'xiongan_20',
    windowStartSeconds: 0,
    durationSeconds: 900,
    controlMode: 'fixed',
    playbackSpeed: 1,
    disturbanceEvents: [{ eventType: 'major_event_closing', intersectionIds: ['demo_1'], vehicleCount: 201 }],
    snapshotIntervalSeconds: 0.2,
  }), /20-200/)
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

test('migrates legacy events to the outer window and exports v6 scene configuration', () => {
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
  assert.equal(SCENARIO_CONFIG_EXPORT_VERSION, 6)
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
