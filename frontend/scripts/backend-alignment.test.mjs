import assert from 'node:assert/strict'
import test from 'node:test'

import { nextTick, ref } from 'vue'

import { useSnapshotMetrics } from '../src/composables/useSnapshotMetrics.ts'
import { buildAlgorithmMetricSeries } from '../src/constants/metricsEvaluation.ts'
import { isBackendControlMode } from '../src/constants/simulationOptions.ts'

test('max_pressure is sent to the backend while preview algorithms keep their fallback', () => {
  assert.equal(isBackendControlMode('fixed'), true)
  assert.equal(isBackendControlMode('max_pressure'), true)
  assert.equal(isBackendControlMode('ippo'), false)
  assert.equal(isBackendControlMode('multi_agent_rl'), false)
})

test('the running backend algorithm owns the real metric series', () => {
  const points = [{
    time: 10,
    algorithm: 'max_pressure',
    avg_waiting_time: 12.5,
    avg_travel_time: 30,
    avg_queue_length: 4.25,
    throughput: 120,
    fuel_consumption: 6.2,
  }]

  const series = buildAlgorithmMetricSeries(points, 'waiting')

  assert.equal(series.find((item) => item.id === 'max_pressure')?.source, 'backend')
  assert.equal(series.find((item) => item.id === 'fixed')?.source, 'derived_mock')
  assert.deepEqual(series.find((item) => item.id === 'max_pressure')?.values, [12.5])
})

test('snapshot metrics use backend evaluation values without local estimation', async () => {
  const sessionId = ref('session-1')
  const snapshot = ref(null)
  const { timeseries } = useSnapshotMetrics(sessionId, snapshot)

  snapshot.value = {
    session_id: 'session-1',
    state: 'RUNNING',
    sequence: 1,
    elapsed_seconds: 10,
    duration_seconds: 60,
    progress: 0.16,
    official_time: '08:00:10',
    intersections: {},
    vehicles: [],
    events: [],
    metrics: {
      active_vehicles: 10,
      departed_vehicles: 10,
      arrived_vehicles: 2,
      remaining_vehicles: 8,
      halting_vehicles: 9,
      total_waiting_time: 999,
      mean_speed: 5,
    },
    evaluation: {
      episode_id: 'session-1',
      algorithm: 'max_pressure',
      avg_waiting_time: 12.5,
      avg_travel_time: 30,
      avg_queue_length: 4.25,
      throughput: 120,
      fuel_consumption: 6.2,
      avg_decision_latency_ms: 1.5,
      departed: 10,
      arrived: 2,
      finished: false,
    },
    error: null,
  }
  await nextTick()

  assert.deepEqual(timeseries.value.series, [{
    time: 10,
    algorithm: 'max_pressure',
    avg_waiting_time: 12.5,
    avg_travel_time: 30,
    avg_queue_length: 4.25,
    throughput: 120,
    fuel_consumption: 6.2,
  }])
})
