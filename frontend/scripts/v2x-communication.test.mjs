import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { nextTick, ref } from 'vue'

import { useSnapshotMetrics } from '../src/composables/useSnapshotMetrics.ts'
import {
  formatCommunicationClock,
  formatLatencyLabel,
  formatV2XEndpoint,
  latencyMsFromMessageAge,
  resolveV2XLinkType,
  resolveV2XMessageMeta,
  resolveV2XStatus,
  V2X_DIRECTION_FILTERS,
  V2X_LINK_FILTERS,
  v2xCommunicationEmptyText,
} from '../src/utils/v2xCommunication.ts'

function event(overrides = {}) {
  return {
    schema: 'cov2x.v2x.event',
    schema_version: '1.0',
    sequence: 1,
    event: 'SEND',
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
    ...overrides,
  }
}

function snapshot(v2xEvents, extras = {}) {
  return {
    session_id: 'session-v2x',
    state: 'RUNNING',
    sequence: 1,
    elapsed_seconds: 5,
    duration_seconds: 60,
    progress: 1 / 12,
    official_time: '07:00:05',
    intersections: {},
    vehicles: [],
    events: [],
    v2x_events: v2xEvents,
    metrics: {},
    error: null,
    evaluation: { episode_id: 'session-v2x', algorithm: 'cov2x' },
    ...extras,
  }
}

test('maps CoV2X roles onto the real link types instead of a four-stage V2I/I2V shortcut', () => {
  assert.equal(resolveV2XLinkType('vehicle', 'cloud'), 'V2C')
  assert.equal(resolveV2XLinkType('road', 'cloud'), 'I2C')
  assert.equal(resolveV2XLinkType('cloud', 'road'), 'C2I')
  assert.equal(resolveV2XLinkType('cloud', 'vehicle'), 'C2V')
  assert.equal(resolveV2XLinkType('road', 'vehicle'), 'I2V')
  assert.equal(resolveV2XLinkType('vehicle', 'road'), 'V2I')
  assert.equal(resolveV2XLinkType('vehicle', 'vehicle'), 'UNKNOWN')
  assert.equal(resolveV2XLinkType('cloud', 'cloud'), 'UNKNOWN')
  assert.notEqual(resolveV2XLinkType('vehicle', 'cloud'), 'V2I')
  assert.notEqual(resolveV2XLinkType('cloud', 'vehicle'), 'I2V')
})

test('formats endpoints from event ids without inventing a concrete vehicle', () => {
  assert.equal(formatV2XEndpoint('vehicle', 'veh_xxx'), '智能网联车')
  assert.equal(formatV2XEndpoint('vehicle', 'passenger.12'), '智能网联车')
  assert.equal(formatV2XEndpoint('vehicle', 'truck.3'), '智能网联车')
  assert.equal(formatV2XEndpoint('vehicle', 'vehicle'), '智能网联车')
  assert.equal(formatV2XEndpoint('cloud', 'cloud'), '云端控制中心')
  assert.equal(formatV2XEndpoint('road', 'demo_1'), 'RSU-路口1')
})

test('explains RegionalPriorityV1 by destination role without changing the message type', () => {
  assert.equal(resolveV2XMessageMeta('VehicleStateV1').tag, '车辆状态')
  assert.equal(resolveV2XMessageMeta('VehicleStateV1').title, '车辆向云端上报行驶状态')
  assert.equal(resolveV2XMessageMeta('IntersectionSummaryV1').tag, '路口状态')
  assert.equal(
    resolveV2XMessageMeta('RegionalPriorityV1', 'road').tag,
    '路口协调',
  )
  assert.equal(
    resolveV2XMessageMeta('RegionalPriorityV1', 'road').title,
    '云端向路口下发协调优先级',
  )
  assert.equal(
    resolveV2XMessageMeta('RegionalPriorityV1', 'vehicle').tag,
    '车辆协调',
  )
  assert.equal(
    resolveV2XMessageMeta('RegionalPriorityV1', 'vehicle').title,
    '云端向车辆下发协调优先级',
  )
  assert.equal(resolveV2XMessageMeta('SPaTV2').tag, '红绿灯配时')
  assert.equal(resolveV2XMessageMeta('MAPV1').tag, '路口地图')
  assert.equal(resolveV2XMessageMeta('VehicleAdviceV1').title, 'VehicleAdviceV1')
})

test('shows clock as hour-minute-second and ideal delay as instant', () => {
  assert.equal(formatCommunicationClock(5, '07:00:05', 5), '07:00:05')
  assert.equal(formatCommunicationClock(183, '07:03:03', 183), '07:03:03')
  assert.equal(latencyMsFromMessageAge(0), 0)
  assert.equal(formatLatencyLabel(0), '即时')
  assert.equal(formatLatencyLabel(12), '12')
  assert.equal(resolveV2XStatus('SEND'), 'sending')
  assert.equal(resolveV2XStatus('DELIVER'), 'success')
  assert.equal(resolveV2XStatus('CONSUME'), 'success')
  assert.equal(resolveV2XStatus('TTL_EXPIRED'), 'failed')
})

test('empty copy distinguishes CoV2X waiting from non-collaborative algorithms', () => {
  assert.equal(v2xCommunicationEmptyText('cov2x'), '等待车路云通信数据...')
  assert.equal(v2xCommunicationEmptyText('fixed'), '当前管控算法未启用车路云协同通信')
  assert.equal(v2xCommunicationEmptyText('max_pressure'), '当前管控算法未启用车路云协同通信')
  assert.equal(v2xCommunicationEmptyText('sotl'), '当前管控算法未启用车路云协同通信')
  assert.equal(v2xCommunicationEmptyText('ippo'), '当前管控算法未启用车路云协同通信')
  assert.equal(v2xCommunicationEmptyText('mappo'), '当前管控算法未启用车路云协同通信')
})

test('communication log keeps one row per message_id and updates lifecycle in place', async () => {
  const sessionId = ref('session-v2x')
  const current = ref(null)
  const controlMode = ref('cov2x')
  const { logEntries } = useSnapshotMetrics(sessionId, current, undefined, controlMode)
  const send = event()
  const deliver = event({ sequence: 2, event: 'DELIVER', message_age_s: 0 })
  const consume = event({ sequence: 3, event: 'CONSUME', message_age_s: 0 })

  current.value = snapshot([send])
  await nextTick()
  assert.equal(logEntries.value.length, 1)
  assert.equal(logEntries.value[0].status, 'sending')
  assert.equal(logEntries.value[0].linkType, 'V2C')
  assert.equal(logEntries.value[0].source, '智能网联车')
  assert.equal(logEntries.value[0].destination, '云端控制中心')
  assert.equal(logEntries.value[0].latencyMs, 0)
  assert.equal(logEntries.value[0].timeLabel, '07:00:05')

  current.value = snapshot([send, deliver, consume], { sequence: 2 })
  await nextTick()
  assert.equal(logEntries.value.length, 1)
  assert.equal(logEntries.value[0].status, 'success')
  assert.equal(logEntries.value[0].eventState, 'CONSUME')
  assert.equal(logEntries.value[0].latencyMs, 0)
})

test('renders the five CoV2X directions from native events only', async () => {
  const sessionId = ref('session-v2x')
  const current = ref(null)
  const controlMode = ref('cov2x')
  const { logEntries } = useSnapshotMetrics(sessionId, current, undefined, controlMode)

  current.value = snapshot([
    event(),
    event({
      sequence: 2,
      message_type: 'IntersectionSummaryV1',
      message_id: 'session-v2x:0:intersection:demo_1',
      source_role: 'road',
      source_id: 'demo_1',
      destination_role: 'cloud',
      destination_id: 'cloud',
    }),
    event({
      sequence: 3,
      message_type: 'RegionalPriorityV1',
      message_id: 'session-v2x:0:cloud-road:demo_1',
      source_role: 'cloud',
      source_id: 'cloud',
      destination_role: 'road',
      destination_id: 'demo_1',
      causal_parent_ids: ['session-v2x:0:intersection:demo_1'],
    }),
    event({
      sequence: 4,
      message_type: 'RegionalPriorityV1',
      message_id: 'session-v2x:0:cloud-vehicle:demo_1',
      source_role: 'cloud',
      source_id: 'cloud',
      destination_role: 'vehicle',
      destination_id: 'vehicle',
    }),
    event({
      sequence: 5,
      message_type: 'SPaTV2',
      message_id: 'session-v2x:0:spat:demo_1',
      source_role: 'road',
      source_id: 'demo_1',
      destination_role: 'vehicle',
      destination_id: 'vehicle',
    }),
    event({
      sequence: 6,
      message_type: 'MAPV1',
      message_id: 'session-v2x:0:map:demo_1',
      source_role: 'road',
      source_id: 'demo_1',
      destination_role: 'vehicle',
      destination_id: 'vehicle',
    }),
  ])
  await nextTick()

  const byId = Object.fromEntries(logEntries.value.map((row) => [row.messageId, row]))
  assert.equal(logEntries.value.length, 6)
  assert.equal(byId['session-v2x:0:veh-1'].linkType, 'V2C')
  assert.equal(byId['session-v2x:0:intersection:demo_1'].linkType, 'I2C')
  assert.equal(byId['session-v2x:0:cloud-road:demo_1'].linkType, 'C2I')
  assert.equal(byId['session-v2x:0:cloud-road:demo_1'].message, '云端向路口下发协调优先级')
  assert.equal(byId['session-v2x:0:cloud-road:demo_1'].messageTag, '路口协调')
  assert.equal(byId['session-v2x:0:cloud-road:demo_1'].destination, 'RSU-路口1')
  assert.equal(byId['session-v2x:0:cloud-vehicle:demo_1'].linkType, 'C2V')
  assert.equal(byId['session-v2x:0:cloud-vehicle:demo_1'].message, '云端向车辆下发协调优先级')
  assert.equal(byId['session-v2x:0:cloud-vehicle:demo_1'].destination, '智能网联车')
  assert.equal(byId['session-v2x:0:spat:demo_1'].linkType, 'I2V')
  assert.equal(byId['session-v2x:0:map:demo_1'].linkType, 'I2V')
  assert.equal(
    byId['session-v2x:0:cloud-road:demo_1'].causalParentIds[0],
    'session-v2x:0:intersection:demo_1',
  )
  assert.equal(logEntries.value.some((row) => row.messageType === 'VehicleAdviceV1'), false)
  assert.equal(logEntries.value.some((row) => row.linkType === 'V2V'), false)
  assert.equal(logEntries.value.some((row) => row.linkType === 'C2C'), false)
})

test('non-CoV2X algorithms never surface invented communication logs', async () => {
  const sessionId = ref('session-fixed')
  const current = ref(null)
  const controlMode = ref('max_pressure')
  const { logEntries } = useSnapshotMetrics(sessionId, current, undefined, controlMode)

  current.value = snapshot([event()], {
    session_id: 'session-fixed',
    evaluation: { episode_id: 'session-fixed', algorithm: 'max_pressure' },
  })
  await nextTick()
  assert.deepEqual(logEntries.value, [])
})

test('communication panel filters follow the real CoV2X topology', async () => {
  const panel = await readFile(
    new URL('../src/components/dashboard/CenterCommunicationPanel.vue', import.meta.url),
    'utf8',
  )
  const types = await readFile(
    new URL('../src/types/collaboration.ts', import.meta.url),
    'utf8',
  )
  assert.match(panel, /V2X_DIRECTION_FILTERS/)
  assert.match(panel, /V2X_LINK_FILTERS/)
  assert.match(panel, /v2xCommunicationEmptyText/)
  assert.equal(panel.includes('延迟'), false)
  assert.equal(panel.includes('formatLatencyLabel'), false)
  assert.equal(
    V2X_DIRECTION_FILTERS.map((item) => item.value).join(','),
    'all,vehicle->cloud,road->cloud,cloud->road,cloud->vehicle,road->vehicle,vehicle->road',
  )
  assert.deepEqual([...V2X_LINK_FILTERS], ['V2C', 'I2C', 'C2I', 'C2V', 'I2V', 'V2I', 'UNKNOWN'])
  assert.equal(panel.includes('vehicle->vehicle'), false)
  assert.equal(panel.includes('cloud->cloud'), false)
  assert.equal(panel.includes("'V2V'"), false)
  assert.equal(panel.includes("'C2C'"), false)
  assert.match(types, /'V2C'/)
  assert.match(types, /'C2V'/)
  assert.equal(types.includes("'V2V'"), false)
  assert.equal(types.includes("'C2C'"), false)
})
