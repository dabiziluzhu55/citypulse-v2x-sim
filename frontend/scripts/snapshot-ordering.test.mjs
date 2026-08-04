import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldApplySimulationSnapshot } from '../src/utils/snapshotOrdering.ts'

function snapshot(sequence, elapsedSeconds, state = 'RUNNING', sessionId = 'session-1') {
  return {
    session_id: sessionId,
    sequence,
    elapsed_seconds: elapsedSeconds,
    state,
  }
}

test('accepts only monotonic snapshots for the active session', () => {
  const current = snapshot(12, 2.4)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(13, 2.6), 'session-1'), true)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(11, 2.2), 'session-1'), false)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(12, 2.4), 'session-1'), false)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(12, 2.3), 'session-1'), false)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(13, 2.6, 'RUNNING', 'old-session'), 'session-1'), false)
})

test('accepts playback state transitions published at the same sequence and time', () => {
  const current = snapshot(40, 8, 'RUNNING')
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(40, 8, 'PAUSED'), 'session-1'), true)
  assert.equal(shouldApplySimulationSnapshot(snapshot(40, 8, 'PAUSED'), snapshot(40, 8, 'RUNNING'), 'session-1'), true)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(40, 8, 'COMPLETED'), 'session-1'), true)
})
