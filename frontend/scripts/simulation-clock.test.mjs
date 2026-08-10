import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ConfirmedSimulationClock,
  formatOfficialTimeSeconds,
  parseOfficialTimeSeconds,
} from '../src/utils/confirmedSimulationClock.ts'

test('parses and formats confirmed official time without inventing seconds', () => {
  assert.equal(parseOfficialTimeSeconds('14:30:05'), 52_205)
  assert.equal(parseOfficialTimeSeconds('2026-08-07T14:30:05'), 52_205)
  assert.equal(formatOfficialTimeSeconds(52_205), '14:30:05')
  assert.equal(parseOfficialTimeSeconds('invalid'), null)
})

test('smooths only between confirmed snapshots and never runs beyond the target', () => {
  const clock = new ConfirmedSimulationClock()
  assert.equal(clock.accept({ sequence: 1, officialTime: '14:30:00', state: 'RUNNING', arrivalTimeMs: 0 }), true)
  assert.equal(clock.valueAt(1_000), 52_200)
  assert.equal(clock.accept({ sequence: 2, officialTime: '14:30:01', state: 'RUNNING', arrivalTimeMs: 1_000 }), true)
  assert.ok((clock.valueAt(1_500) ?? 0) >= 52_200)
  assert.ok((clock.valueAt(1_500) ?? 0) <= 52_201)
  assert.equal(clock.valueAt(5_000), 52_201)
})

test('rejects stale snapshots and stops interpolation when paused', () => {
  const clock = new ConfirmedSimulationClock()
  clock.accept({ sequence: 2, officialTime: '23:59:59', state: 'RUNNING', arrivalTimeMs: 0 })
  assert.equal(clock.accept({ sequence: 1, officialTime: '23:59:58', state: 'RUNNING', arrivalTimeMs: 100 }), false)
  clock.accept({ sequence: 3, officialTime: '00:00:00', state: 'PAUSED', arrivalTimeMs: 500 })
  assert.equal(clock.valueAt(2_000), 86_400)
})
