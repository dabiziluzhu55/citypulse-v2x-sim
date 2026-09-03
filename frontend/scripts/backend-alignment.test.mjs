import assert from 'node:assert/strict'
import test from 'node:test'

import { nextTick, ref } from 'vue'

import { useSnapshotMetrics } from '../src/composables/useSnapshotMetrics.ts'
import { buildAlgorithmMetricSeries } from '../src/constants/metricsEvaluation.ts'
import {
  isBackendControlMode,
  requireAvailableControlMode,
  resolveDashboardControlModes,
} from '../src/constants/simulationOptions.ts'

test('exposes all six algorithms registered by the main backend', () => {
  assert.equal(isBackendControlMode('fixed'), true)
  assert.equal(isBackendControlMode('max_pressure'), true)
  assert.equal(isBackendControlMode('sotl'), true)
  assert.equal(isBackendControlMode('ippo'), true)
  assert.equal(isBackendControlMode('mappo'), true)
  assert.equal(isBackendControlMode('cov2x'), true)
  assert.equal(isBackendControlMode('multi_agent_rl'), false)
  assert.deepEqual(
    resolveDashboardControlModes(['sotl', 'ippo', 'mappo', 'cov2x', 'fixed']).map((item) => item.value),
    ['fixed', 'sotl', 'ippo', 'mappo', 'cov2x'],
  )
  assert.equal(requireAvailableControlMode('sotl', ['fixed', 'sotl']), 'sotl')
  assert.throws(
    () => requireAvailableControlMode('sotl', ['fixed']),
    /后端未提供管控算法：sotl/,
  )
  assert.equal(requireAvailableControlMode('ippo', ['fixed', 'ippo']), 'ippo')
  assert.equal(requireAvailableControlMode('mappo', ['fixed', 'mappo']), 'mappo')
  assert.equal(requireAvailableControlMode('cov2x', ['fixed', 'cov2x']), 'cov2x')
})

test('the running backend algorithm owns the real metric series', () => {
  const points = [{
    time: 10,
    algorithm: 'max_pressure',
    path_avg_speed_kmh: null,
    travel_time_index: null,
    delay_time_proportion: null,
    traffic_performance_index: null,
    traffic_state: null,
    tpi_method: null,
    avg_stops_per_vehicle: null,
    regional_max_queue_length_m: null,
    regional_max_queue_intersection_id: null,
    regional_max_queue_lane_id: null,
    regional_max_queue_sim_time_s: null,
    spillback_rate: null,
    avg_waiting_time: 12.5,
    avg_travel_time: 30,
    avg_queue_length: 4.25,
    throughput: 120,
    fuel_consumption: 6.2,
  }]

  const series = buildAlgorithmMetricSeries(points, 'waiting')

  assert.equal(series.find((item) => item.id === 'max_pressure')?.source, 'backend')
  assert.equal(series.find((item) => item.id === 'fixed')?.source, 'missing')
  assert.equal(series.find((item) => item.id === 'sotl')?.source, 'missing')
  assert.equal(series.find((item) => item.id === 'ippo')?.source, 'missing')
  assert.equal(series.find((item) => item.id === 'mappo')?.source, 'missing')
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
    path_avg_speed_kmh: null,
    travel_time_index: null,
    delay_time_proportion: null,
    traffic_performance_index: null,
    traffic_state: null,
    tpi_method: null,
    avg_stops_per_vehicle: null,
    regional_max_queue_length_m: null,
    regional_max_queue_intersection_id: null,
    regional_max_queue_lane_id: null,
    regional_max_queue_sim_time_s: null,
    spillback_rate: null,
    avg_waiting_time: 12.5,
    avg_travel_time: 30,
    avg_queue_length: 4.25,
    throughput: 120,
    fuel_consumption: 6.2,
    fuel_intensity_L_per_100km: 6.2,
    hard_braking_events: null,
    hard_braking_rate: null,
    finished: false,
    metric_sources: {},
    warnings: [],
  }])
})

test('communication log consumes real V2X send events and ignores delivery duplicates', async () => {
  const sessionId = ref('session-v2x')
  const snapshot = ref(null)
  const { logEntries } = useSnapshotMetrics(sessionId, snapshot)
  const common = {
    schema: 'cov2x.v2x.event',
    schema_version: '1.0',
    sequence: 1,
    message_type: 'VehicleStateV1',
    message_id: 'session-v2x:0:veh-1',
    episode_id: 'session-v2x',
    snapshot_id: 'session-v2x:0',
    source_role: 'vehicle',
    source_id: 'veh-1',
    destination_role: 'cloud',
    destination_id: 'cloud',
    logical_phase: 'state',
    event_time_s: 5,
    sent_time_s: 5,
    message_age_s: 0,
    ttl_s: 5,
    expires_at_s: 10,
    causal_parent_ids: [],
    payload_fields: ['vehicle_id'],
    drop_reason: null,
  }
  snapshot.value = {
    session_id: 'session-v2x', state: 'RUNNING', sequence: 1,
    elapsed_seconds: 5, duration_seconds: 60, progress: 1 / 12,
    official_time: '07:00:05', intersections: {}, vehicles: [], events: [],
    v2x_events: [{ ...common, event: 'SEND' }, { ...common, event: 'DELIVER' }],
    metrics: {}, error: null,
  }
  await nextTick()

  assert.equal(logEntries.value.length, 1)
  assert.equal(logEntries.value[0].message, '车辆状态上报')
  assert.equal(logEntries.value[0].sourceRole, 'vehicle')
  assert.equal(logEntries.value[0].destinationRole, 'cloud')
  assert.equal(logEntries.value[0].destination, 'cloud')
  assert.equal(logEntries.value[0].linkType, 'V2I')
  assert.equal(logEntries.value[0].messageTag, 'CV Status')
  assert.equal(logEntries.value[0].status, 'success')
  assert.equal(logEntries.value[0].latencyMs, 0)
})
