import assert from 'node:assert/strict'
import test from 'node:test'

import {
  appendPlaybackRateSample,
  calculatePlaybackRate,
} from '../src/utils/playbackRate.ts'

test('calculates achieved simulation playback speed from wall time', () => {
  const samples = [
    { sessionId: 'run-1', wallTimeMs: 1_000, elapsedSeconds: 10 },
    { sessionId: 'run-1', wallTimeMs: 2_000, elapsedSeconds: 13 },
  ]
  assert.equal(calculatePlaybackRate(samples), 3)
})

test('waits for a stable measurement window', () => {
  const samples = [
    { sessionId: 'run-1', wallTimeMs: 1_000, elapsedSeconds: 10 },
    { sessionId: 'run-1', wallTimeMs: 1_200, elapsedSeconds: 11 },
  ]
  assert.equal(calculatePlaybackRate(samples), null)
})

test('resets the rolling window when the session or timeline changes', () => {
  const initial = [{ sessionId: 'run-1', wallTimeMs: 1_000, elapsedSeconds: 10 }]
  assert.deepEqual(appendPlaybackRateSample(initial, {
    sessionId: 'run-2', wallTimeMs: 2_000, elapsedSeconds: 2,
  }), [{ sessionId: 'run-2', wallTimeMs: 2_000, elapsedSeconds: 2 }])
  assert.deepEqual(appendPlaybackRateSample(initial, {
    sessionId: 'run-1', wallTimeMs: 2_000, elapsedSeconds: 5,
  }), [{ sessionId: 'run-1', wallTimeMs: 2_000, elapsedSeconds: 5 }])
})
