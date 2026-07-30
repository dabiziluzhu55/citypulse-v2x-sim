import assert from 'node:assert/strict'
import test from 'node:test'

import {
  appendRealEvaluationPoint,
  comparisonChangeRequiresConfirmation,
  createScenarioFingerprint,
} from '../src/composables/useEvaluationComparison.ts'
import {
  buildAlgorithmMetricSeries,
  evaluationTimes,
} from '../src/constants/metricsEvaluation.ts'

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
    snapshot_interval_seconds: 0.2,
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
})

test('replaces duplicate timestamps and caps stored real points', () => {
  const initial = [{ time: 1, algorithm: 'fixed', avg_waiting_time: 1, avg_queue_length: 1, throughput: 1 }]
  const replaced = appendRealEvaluationPoint(initial, { ...initial[0], avg_queue_length: 9 })
  assert.equal(replaced.length, 1)
  assert.equal(replaced[0].avg_queue_length, 9)
  const capped = appendRealEvaluationPoint(replaced, { ...initial[0], time: 2 }, 1)
  assert.deepEqual(capped.map((point) => point.time), [2])
})
