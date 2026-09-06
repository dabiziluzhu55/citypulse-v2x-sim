import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  appendRealEvaluationPoint,
  comparisonContractDifferences,
  comparisonChangeRequiresConfirmation,
  createScenarioFingerprint,
  evaluationPoint,
  parseComparisonContract,
  requiresFinalEvaluationRecovery,
  updateStoredRunFromSnapshot,
} from '../src/composables/useEvaluationComparison.ts'
import {
  EVALUATION_AXIS,
  EVALUATION_METRICS,
  METRICS_ALGORITHMS,
  buildAlgorithmMetricSeries,
  evaluationTimes,
} from '../src/constants/metricsEvaluation.ts'
import {
  formatIntersectionLabels,
  formatScenarioPresetLabel,
  formatSimulationWindow,
} from '../src/utils/scenarioDisplay.ts'

const evaluationSource = await readFile(
  new URL('../src/composables/useEvaluationComparison.ts', import.meta.url),
  'utf8',
)
const simulationStoreSource = await readFile(
  new URL('../src/composables/useSimulationStore.ts', import.meta.url),
  'utf8',
)
const homePageSource = await readFile(new URL('../src/pages/HomePage.vue', import.meta.url), 'utf8')

test('uses concrete algorithm names in the evaluation legend', () => {
  assert.deepEqual(
    METRICS_ALGORITHMS.map((item) => item.shortLabel),
    ['固定配时', 'Max Pressure', 'SOTL', 'IPPO', 'MAPPO', 'CoV2X'],
  )
})

test('uses one fixed zero-to-fifteen-minute axis and backend metric units', () => {
  assert.deepEqual(EVALUATION_AXIS, { minMinutes: 0, maxMinutes: 15, intervalMinutes: 3 })
  assert.deepEqual(EVALUATION_METRICS.map((item) => item.unit), [
    '辆/进口车道', '秒', 'L/100km',
  ])
})

test('prompts only when an accepted comparison with data would change', () => {
  assert.equal(comparisonChangeRequiresConfirmation('same', 'same', true), false)
  assert.equal(comparisonChangeRequiresConfirmation('old', 'new', false), false)
  assert.equal(comparisonChangeRequiresConfirmation('', 'new', true), false)
  assert.equal(comparisonChangeRequiresConfirmation('old', 'new', true), true)
})

function payload(overrides = {}) {
  return {
    scenario_preset_id: 'xiongan_20',
    period: 'morning_peak',
    origins: { demo_2: ['west'] },
    window_start_seconds: 0,
    duration_seconds: 900,
    control_mode: 'fixed',
    seed: 42,
    step_length: 0.2,
    realtime: true,
    gui: false,
    snapshot_interval_seconds: 0.5,
    disturbance_targets: [],
    playback_speed: 1,
    ...overrides,
  }
}

test('comparison fingerprint ignores algorithm and playback speed', () => {
  const fixed = createScenarioFingerprint(payload(), 'demo_2')
  const sotl = createScenarioFingerprint(payload({ control_mode: 'sotl', playback_speed: 5 }), 'demo_2')
  assert.equal(fixed, sotl)
})

test('comparison fingerprint ignores viewed intersection, map transport, and realtime flags', () => {
  const baseline = createScenarioFingerprint(payload(), 'demo_2')
  const changedView = createScenarioFingerprint(payload({
    snapshot_interval_seconds: 2,
    realtime: false,
    gui: true,
  }), 'demo_8')
  assert.equal(baseline, changedView)
})

test('comparison fingerprint ignores generated disturbance ids and target order', () => {
  const first = payload({
    disturbance_targets: [
      { event_type: 'lane_closure', event_id: 'runtime-1', intersection_id: 'demo_2', start_seconds: 60, end_seconds: 300, lane_ids: ['a'] },
      { event_type: 'speed_limit', event_id: 'runtime-2', intersection_id: 'demo_2', start_seconds: 90, end_seconds: 240, lane_ids: ['b'], max_speed: 5 },
    ],
  })
  const second = payload({
    disturbance_targets: [
      { event_type: 'speed_limit', event_id: 'different-2', intersection_id: 'demo_2', start_seconds: 90, end_seconds: 240, lane_ids: ['b'], max_speed: 5 },
      { event_type: 'lane_closure', event_id: 'different-1', intersection_id: 'demo_2', start_seconds: 60, end_seconds: 300, lane_ids: ['a'] },
    ],
  })
  assert.equal(
    createScenarioFingerprint(first, 'demo_2'),
    createScenarioFingerprint(second, 'demo_2'),
  )
})

test('comparison fingerprint deduplicates unordered origin and lane collections', () => {
  const first = payload({
    origins: { demo_2: ['west', 'north'] },
    disturbance_targets: [{
      event_type: 'lane_closure', event_id: 'runtime-1', intersection_id: 'demo_8',
      start_seconds: 60, end_seconds: 300, lane_ids: ['lane-b', 'lane-a'],
    }],
  })
  const second = payload({
    origins: { demo_2: ['north', 'west', 'north'] },
    disturbance_targets: [{
      event_type: 'lane_closure', event_id: 'runtime-2', intersection_id: 'demo_8',
      start_seconds: 60, end_seconds: 300, lane_ids: ['lane-a', 'lane-b', 'lane-a'],
    }],
  })
  assert.equal(
    createScenarioFingerprint(first, ['demo_8', 'demo_2', 'demo_8']),
    createScenarioFingerprint(second, ['demo_2', 'demo_8']),
  )
})

test('comparison fingerprint changes for result-affecting configuration', () => {
  const baseline = createScenarioFingerprint(payload(), ['demo_2', 'demo_8'])
  assert.notEqual(baseline, createScenarioFingerprint(payload({ seed: 43 }), ['demo_2', 'demo_8']))
  assert.notEqual(baseline, createScenarioFingerprint(payload(), ['demo_2']))
  assert.notEqual(baseline, createScenarioFingerprint(payload({ step_length: 0.1 }), ['demo_2', 'demo_8']))
})

test('reports only result-affecting contract differences', () => {
  const baseline = createScenarioFingerprint(payload(), ['demo_2'])
  const candidate = createScenarioFingerprint(payload({ period: 'off_peak', duration_seconds: 60 }), ['demo_2'])
  assert.deepEqual(comparisonContractDifferences(baseline, candidate), [
    '交通时段：早高峰 → 平峰',
    '展示窗口：07:00-07:15 → 14:30-14:31',
  ])
})

test('describes changed event and clock windows in operator-facing terms', () => {
  const baseline = createScenarioFingerprint(payload(), ['demo_2'])
  const candidate = createScenarioFingerprint(payload({
    period: 'off_peak',
    window_start_seconds: 0,
    duration_seconds: 900,
    disturbance_targets: [{
      event_type: 'accident', event_id: 'runtime', intersection_id: 'demo_8',
      start_seconds: 60, end_seconds: 300, lane_id: 'lane-a', position_ratio: 0.8,
    }],
  }), ['demo_2'])
  assert.deepEqual(comparisonContractDifferences(baseline, candidate), [
    '交通时段：早高峰 → 平峰',
    '展示窗口：07:00-07:15 → 14:30-14:45',
    '扰动事件：无 → 路口8 事故',
  ])
})

test('formats scenario ids, intersections, and period-relative windows for operators', () => {
  assert.equal(formatScenarioPresetLabel('xiongan_20'), '雄安20路口路网')
  assert.equal(formatScenarioPresetLabel('east_dense'), '校园周边场景')
  assert.equal(formatScenarioPresetLabel('west_dense'), '窄路密网片区场景')
  assert.equal(formatIntersectionLabels(['demo_10', 'demo_2', 'demo_1', 'demo_2']), '路口1、路口2、路口10')
  assert.equal(formatSimulationWindow('morning_peak', 0, 900), '07:00-07:15')
  assert.equal(formatSimulationWindow('morning_peak', 300, 900), '07:05-07:20')
  assert.equal(formatSimulationWindow('off_peak', 0, 900), '14:30-14:45')
  assert.equal(formatSimulationWindow('off_peak', 300, 900), '14:35-14:50')
  assert.equal(formatSimulationWindow('evening_peak', 0, 900), '17:30-17:45')
  assert.equal(formatSimulationWindow('evening_peak', 300, 900), '17:35-17:50')
})

test('binds the accepted presentation generation before registering comparison state', () => {
  const launchBlock = simulationStoreSource.slice(
    simulationStoreSource.indexOf('async function launchRun'),
    simulationStoreSource.indexOf('function clearStatusError'),
  )
  assert.ok(launchBlock.indexOf('onSessionAccepted?.(result)') > launchBlock.indexOf('bindSession('))
  assert.match(homePageSource, /const frozenFocusIntersectionId = activeIntersectionId\.value/)
  assert.match(homePageSource, /beginComparisonRun\(result\.session_id, payload, controlledIntersectionIds\)/)
  assert.doesNotMatch(evaluationSource, /restored-session:/)
  assert.match(evaluationSource, /readOnly: true/)
  assert.match(evaluationSource, /citypulse\.evaluation_comparison\.v3/)
  assert.match(evaluationSource, /citypulse\.evaluation_comparison\.v2/)
  assert.match(evaluationSource, /activeComparisonRuns/)
  assert.match(evaluationSource, /activeComparisonContract/)
  assert.match(evaluationSource, /point\.finished === true/)
  assert.doesNotMatch(evaluationSource, /run\.state === 'COMPLETED'/)
  assert.match(homePageSource, /:comparison-runs="activeComparisonRuns"/)
  assert.match(homePageSource, /:comparison-contract="activeComparisonContract"/)
})

test('tracks each algorithm run independently and records terminal failure metadata', () => {
  const run = {
    sessionId: 'mappo-session',
    algorithm: 'mappo',
    updatedAt: 1,
    points: [],
    state: 'STARTING',
    progress: 0,
    lastSequence: -1,
    startedAt: 1,
    completedAt: null,
    error: null,
  }
  const failed = updateStoredRunFromSnapshot(run, {
    session_id: 'mappo-session',
    state: 'FAILED',
    progress: 0,
    sequence: 0,
    error: "No module named 'torch'",
  }, 2)
  assert.equal(failed.state, 'FAILED')
  assert.equal(failed.progress, 0)
  assert.equal(failed.lastSequence, 0)
  assert.equal(failed.completedAt, 2)
  assert.equal(failed.error, "No module named 'torch'")
  assert.equal(run.state, 'STARTING')

  const ingestBlock = evaluationSource.slice(
    evaluationSource.indexOf('function ingest'),
    evaluationSource.indexOf('const activeGroup'),
  )
  assert.ok(ingestBlock.indexOf('storeRunSnapshot(next)') < ingestBlock.indexOf('requiresFinalEvaluationRecovery(next)'))
  assert.match(evaluationSource, /group\.runs\[payload\.control_mode\] = \{[\s\S]*points: \[\]/)
})

test('keeps only real algorithm values and leaves unrun algorithms empty', () => {
  const points = [
    { time: 0, algorithm: 'fixed', avg_waiting_time: 2, avg_queue_length: 3, throughput: 4, fuel_consumption: 5 },
    { time: 1, algorithm: 'sotl', avg_waiting_time: 1, avg_queue_length: 2, throughput: 5, fuel_consumption: 4 },
  ]
  assert.deepEqual(evaluationTimes(points), [0, 1])
  const series = buildAlgorithmMetricSeries(points, 'queue')
  assert.deepEqual(series.find((item) => item.id === 'fixed').values, [3, null])
  assert.deepEqual(series.find((item) => item.id === 'sotl').values, [null, 2])
  assert.equal(series.find((item) => item.id === 'max_pressure').source, 'missing')
  assert.equal(series.find((item) => item.id === 'ippo').source, 'missing')
  assert.equal(series.find((item) => item.id === 'mappo').source, 'missing')
})

test('replaces duplicate timestamps and caps stored real points', () => {
  const initial = [{ time: 1, algorithm: 'fixed', avg_waiting_time: 1, avg_queue_length: 1, throughput: 1 }]
  const replaced = appendRealEvaluationPoint(initial, { ...initial[0], avg_queue_length: 9 })
  assert.equal(replaced.length, 1)
  assert.equal(replaced[0].avg_queue_length, 9)
  const capped = appendRealEvaluationPoint(replaced, { ...initial[0], time: 2 }, 1)
  assert.deepEqual(capped.map((point) => point.time), [2])
})

function snapshot(state, elapsedSeconds, finished) {
  const evaluation = {
    episode_id: 'session-1',
    algorithm: 'fixed',
    avg_waiting_time: finished ? 7 : 9,
    avg_travel_time: finished ? 20 : 18,
    avg_queue_length: 2.5,
    throughput: 100,
    fuel_consumption: null,
    avg_decision_latency_ms: null,
    departed: 10,
    arrived: 8,
    completion_rate: 0.8,
    metric_sources: finished ? { avg_waiting_time_s: 'tripinfo_completed' } : {},
    warnings: finished ? ['fuel unavailable'] : [],
    finished,
  }
  return {
    session_id: 'session-1', state, sequence: 1, elapsed_seconds: elapsedSeconds,
    duration_seconds: 900, progress: elapsedSeconds / 900, official_time: '07:00:00',
    playback_speed: 1, intersections: {}, vehicles: [], events: [], error: null,
    metrics: { active_vehicles: 0, departed_vehicles: 0, arrived_vehicles: 0, remaining_vehicles: 0, halting_vehicles: 0, total_waiting_time: 0, mean_speed: 0, evaluation },
    evaluation,
  }
}

test('buckets provisional samples every five seconds and preserves null values', () => {
  const point = evaluationPoint(snapshot('RUNNING', 12.8, false))
  assert.equal(point.time, 10)
  assert.equal(point.fuel_consumption, null)
  assert.equal(point.finished, false)
  assert.deepEqual(point.metric_status, {
    queue: 'provisional',
    waiting: 'provisional',
    fuel: 'pending',
  })
})

test('does not plot a contradictory real-time waiting zero as a confirmed value', () => {
  const running = snapshot('RUNNING', 30, false)
  running.evaluation.avg_waiting_time = 0
  running.metrics.evaluation.avg_waiting_time = 0
  running.metrics.total_waiting_time = 12
  running.metrics.halting_vehicles = 3
  const point = evaluationPoint(running)
  assert.equal(point.avg_waiting_time, null)
  assert.equal(point.metric_status.waiting, 'pending')
})

test('preserves a genuine real-time waiting zero when no vehicle is waiting', () => {
  const running = snapshot('RUNNING', 30, false)
  running.evaluation.avg_waiting_time = 0
  running.metrics.evaluation.avg_waiting_time = 0
  const point = evaluationPoint(running)
  assert.equal(point.avg_waiting_time, 0)
  assert.equal(point.metric_status.waiting, 'provisional')
})

test('keeps running null fuel pending even when the backend explains the temporary gap', () => {
  const running = snapshot('RUNNING', 30, false)
  running.evaluation.warnings = ['没有可用的燃油车辆行驶里程，燃油强度记为不可用。']
  running.metrics.evaluation.warnings = [...running.evaluation.warnings]
  const point = evaluationPoint(running)
  assert.equal(point.fuel_consumption, null)
  assert.equal(point.metric_status.fuel, 'pending')
})

test('marks null fuel unavailable only after the TripInfo final frame', () => {
  const completed = snapshot('COMPLETED', 900, true)
  completed.evaluation.warnings = ['没有可用的燃油车辆行驶里程，燃油强度记为不可用。']
  completed.metrics.evaluation = completed.evaluation
  const point = evaluationPoint(completed)
  assert.equal(point.fuel_consumption, null)
  assert.equal(point.metric_status.fuel, 'unavailable')
})

test('keeps a completed MAPPO TripInfo fuel intensity as a final value', () => {
  const completed = snapshot('COMPLETED', 900, true)
  completed.evaluation.algorithm = 'mappo'
  completed.evaluation.fuel_consumption = 15.45
  completed.evaluation.fuel_intensity_L_per_100km = 15.45
  completed.evaluation.metric_sources.fuel_intensity_L_per_100km = 'tripinfo_completed_fuel_vehicles'
  completed.metrics.evaluation = completed.evaluation
  const point = evaluationPoint(completed)
  assert.equal(point.fuel_consumption, 15.45)
  assert.equal(point.metric_status.fuel, 'final')
  assert.equal(point.metric_sources.fuel_intensity_L_per_100km, 'tripinfo_completed_fuel_vehicles')
})

test('normalizes the new fuel alias and preserves optional hard-braking diagnostics', () => {
  const running = snapshot('RUNNING', 30, false)
  running.evaluation.fuel_intensity_L_per_100km = 7.25
  running.evaluation.hard_braking_events = 4
  running.evaluation.hard_braking_rate = 1.5
  running.metrics.evaluation = running.evaluation
  const point = evaluationPoint(running)
  assert.equal(point.fuel_consumption, 7.25)
  assert.equal(point.fuel_intensity_L_per_100km, 7.25)
  assert.equal(point.hard_braking_events, 4)
  assert.equal(point.hard_braking_rate, 1.5)
  assert.equal(point.metric_status.fuel, 'provisional')
})

test('rejects unfinished terminal metrics and records the TripInfo-completed final frame', () => {
  const provisionalTerminal = snapshot('COMPLETED', 900, false)
  assert.equal(requiresFinalEvaluationRecovery(provisionalTerminal), true)
  assert.equal(evaluationPoint(provisionalTerminal), null)

  const completed = snapshot('COMPLETED', 899.9, true)
  const point = evaluationPoint(completed)
  assert.equal(requiresFinalEvaluationRecovery(completed), false)
  assert.equal(point.time, 900)
  assert.equal(point.avg_waiting_time, 7)
  assert.equal(point.metric_sources.avg_waiting_time_s, 'tripinfo_completed')
  assert.deepEqual(point.warnings, ['fuel unavailable'])
  assert.equal(point.metric_status.waiting, 'final')
  assert.equal(point.metric_status.fuel, 'unavailable')
})

test('parses standard comparison fingerprints and rejects unverified sessions', () => {
  const fingerprint = createScenarioFingerprint(payload(), ['demo_2'])
  const contract = parseComparisonContract(fingerprint)
  assert.equal(contract?.scenario_preset_id, 'xiongan_20')
  assert.equal(contract?.period, 'morning_peak')
  assert.equal(contract?.window_start_seconds, 0)
  assert.equal(contract?.duration_seconds, 900)
  assert.equal(parseComparisonContract('unverified-session:abc'), null)
  assert.equal(parseComparisonContract('{not-json'), null)
})

test('a final TripInfo point replaces a provisional point in the same five-second bucket', () => {
  const provisional = { time: 12, algorithm: 'fixed', avg_waiting_time: 0, avg_queue_length: 1, throughput: 4, fuel_consumption: null, finished: false }
  const finalPoint = { ...provisional, time: 14, avg_waiting_time: 7, finished: true }
  assert.deepEqual(appendRealEvaluationPoint([provisional], finalPoint), [finalPoint])
})
