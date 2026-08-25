import assert from 'node:assert/strict'
import test from 'node:test'

import { SignalDisplayTimeline } from '../src/mapv/signalDisplayTimeline.ts'

const intersection = (phase) => ({
  intersection_id: 'demo_1',
  name: 'demo_1',
  current_phase: phase,
  stage: phase === 0 ? 'GREEN' : 'RED',
  phase_name: `phase-${phase}`,
  stage_elapsed: 0,
  queue_length: 0,
  vehicle_count: 0,
  avg_waiting_time: 0,
  avg_speed: 0,
  status: 'free',
})

test('samples the newest signal frame that is not later than vehicle display time', () => {
  const timeline = new SignalDisplayTimeline()
  timeline.push('session-a', 10, [intersection(0)])
  timeline.push('session-a', 11, [intersection(1)])
  timeline.push('session-a', 12, [intersection(2)])

  assert.equal(timeline.sample('session-a', 9.999), null)
  assert.equal(timeline.sample('session-a', 10)?.[0].current_phase, 0)
  assert.equal(timeline.sample('session-a', 11.999)?.[0].current_phase, 1)
  assert.equal(timeline.sample('session-a', 12)?.[0].current_phase, 2)
})

test('clears stale signal frames atomically when a simulation session changes', () => {
  const timeline = new SignalDisplayTimeline()
  timeline.push('session-a', 100, [intersection(3)])
  timeline.push('session-b', 0, [intersection(0)])

  assert.equal(timeline.sample('session-a', 100), null)
  assert.equal(timeline.sample('session-b', 0)?.[0].current_phase, 0)
  timeline.clear()
  assert.equal(timeline.sample('session-b', 0), null)
})
