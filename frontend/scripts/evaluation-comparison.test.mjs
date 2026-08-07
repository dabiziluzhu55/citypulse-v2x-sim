import assert from 'node:assert/strict'
import test from 'node:test'

import {
  appendRealEvaluationPoint,
  comparisonChangeRequiresConfirmation,
  createScenarioFingerprint,
  evaluationPoint,
  requiresFinalEvaluationRecovery,
} from '../src/composables/useEvaluationComparison.ts'
import {
  EVALUATION_AXIS,
  EVALUATION_METRICS,
  METRICS_ALGORITHMS,
  buildAlgorithmMetricSeries,
  evaluationTimes,
} from '../src/constants/metricsEvaluation.ts'

test('uses concrete algorithm names in the evaluation legend', () => {
  assert.deepEqual(
    METRICS_ALGORITHMS.map((item) => item.shortLabel),
    ['固定配时', 'Max Pressure', 'SOTL', 'IPPO', 'MAPPO'],
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
    window_start_seconds: 7 * 3600,
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

test('comparison fingerprint changes for result-affecting configuration', () => {
  const baseline = createScenarioFingerprint(payload(), 'demo_2')
  assert.notEqual(baseline, createScenarioFingerprint(payload({ seed: 43 }), 'demo_2'))
  assert.notEqual(baseline, createScenarioFingerprint(payload(), 'demo_3'))
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

test('marks null fuel unavailable as soon as the backend provides an explicit warning', () => {
  const running = snapshot('RUNNING', 30, false)
  running.evaluation.warnings = ['没有可用的燃油车辆行驶里程，燃油强度记为不可用。']
  running.metrics.evaluation.warnings = [...running.evaluation.warnings]
  const point = evaluationPoint(running)
  assert.equal(point.fuel_consumption, null)
  assert.equal(point.metric_status.fuel, 'unavailable')
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

test('a final TripInfo point replaces a provisional point in the same five-second bucket', () => {
  const provisional = { time: 12, algorithm: 'fixed', avg_waiting_time: 0, avg_queue_length: 1, throughput: 4, fuel_consumption: null, finished: false }
  const finalPoint = { ...provisional, time: 14, avg_waiting_time: 7, finished: true }
  assert.deepEqual(appendRealEvaluationPoint([provisional], finalPoint), [finalPoint])
})
