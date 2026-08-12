import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  freezeDisturbanceRuntimeTargets,
  parseStoredDisturbanceRuntimeTargets,
  runtimeDisturbanceLaneIds,
  runtimeDisturbanceViews,
} from '../src/utils/runtimeDisturbances.ts'

const eventTypes = [
  'lane_closure',
  'speed_limit',
  'accident',
  'major_event_opening',
  'major_event_closing',
]

function target(eventType, index) {
  return {
    event_id: `event-${index}`,
    event_type: eventType,
    intersection_id: `demo_${index + 1}`,
    start_seconds: index * 10,
    end_seconds: index * 10 + 60,
    ...(eventType === 'speed_limit' ? { max_speed: 5 } : {}),
    ...(eventType.startsWith('major_event_') ? { vehicle_count: 30 } : {}),
  }
}

test('freezes all five accepted target types under their backend event ids', () => {
  const frozen = freezeDisturbanceRuntimeTargets(
    'session-a',
    eventTypes.map(target),
  )
  assert.deepEqual(frozen.map((event) => event.eventType), eventTypes)
  assert.deepEqual(frozen.map((event) => event.intersectionId), eventTypes.map((_, index) => `demo_${index + 1}`))
  assert.equal(frozen[1].parameters.max_speed, 5)
})

test('uses snapshot event states and errors instead of a draft clock', () => {
  const targets = freezeDisturbanceRuntimeTargets('session-a', eventTypes.map(target))
  const states = ['SCHEDULED', 'ACTIVE', 'COMPLETED', 'FAILED', 'CANCELLED']
  const views = runtimeDisturbanceViews(targets, {
    session_id: 'session-a',
    state: 'RUNNING',
    sequence: 1,
    elapsed_seconds: 50,
    duration_seconds: 300,
    progress: 1 / 6,
    official_time: '07:00:50',
    playback_speed: 1,
    intersections: {},
    vehicles: [],
    events: states.map((state, index) => ({
      event_id: `event-${index}`,
      event_type: eventTypes[index],
      state,
      error: state === 'FAILED' ? 'lane unavailable' : null,
      details: index === 1 ? { lane_ids: ['edge_0'], max_speed: 4 } : {},
    })),
    metrics: { active_vehicles: 0, departed_vehicles: 0, arrived_vehicles: 0, remaining_vehicles: 0, halting_vehicles: 0, total_waiting_time: 0, mean_speed: 0 },
    error: null,
  })
  assert.deepEqual(views.map((view) => view.state), states)
  assert.equal(views[3].error, 'lane unavailable')
  assert.deepEqual(runtimeDisturbanceLaneIds(views[1]), ['edge_0'])
  assert.equal(views[1].details.max_speed, 4)
})

test('round-trips a session-scoped runtime mapping and rejects malformed storage', () => {
  const targets = freezeDisturbanceRuntimeTargets('session-a', [target('accident', 0)])
  const serialized = JSON.stringify({ version: 1, sessionId: 'session-a', targets })
  assert.deepEqual(parseStoredDisturbanceRuntimeTargets(serialized), {
    version: 1,
    sessionId: 'session-a',
    targets,
  })
  assert.equal(parseStoredDisturbanceRuntimeTargets('{broken'), null)
  assert.equal(parseStoredDisturbanceRuntimeTargets(JSON.stringify({ version: 1, sessionId: 'x', targets })), null)
})

test('updates disturbance visuals without rebuilding the vehicle pose resolver', async () => {
  const source = await readFile(
    new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
    'utf8',
  )
  const watchStart = source.indexOf('watch(runtimeDisturbances')
  assert.ok(watchStart >= 0)
  const watcher = source.slice(watchStart, watchStart + 220)
  assert.match(watcher, /syncRuntimeDisturbanceRoads/)
  assert.doesNotMatch(watcher, /commitViewportTransition|setLanePoseResolver|createIntersectionLanePoseResolver/)
})
