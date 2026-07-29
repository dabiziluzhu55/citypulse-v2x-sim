import assert from 'node:assert/strict'
import test from 'node:test'

import { SIMULATION_TIME_OPTIONS } from '../src/constants/scenarioOptions.ts'

const EXPECTED_LABELS = {
  morning_peak: [
    '7:00-7:15', '7:15-7:30', '7:30-7:45', '7:45-8:00',
    '8:00-8:15', '8:15-8:30', '8:30-8:45', '8:45-9:00',
  ],
  flat: [
    '14:30-14:45', '14:45-15:00', '15:00-15:15', '15:15-15:30',
    '15:30-15:45', '15:45-16:00', '16:00-16:15', '16:15-16:30',
  ],
  evening_peak: [
    '17:30-17:45', '17:45-18:00', '18:00-18:15', '18:15-18:30',
    '18:30-18:45', '18:45-19:00', '19:00-19:15', '19:15-19:30',
  ],
}

test('provides eight exact 15-minute windows for each official period', () => {
  assert.equal(SIMULATION_TIME_OPTIONS.length, 24)
  for (const [flowMode, labels] of Object.entries(EXPECTED_LABELS)) {
    const options = SIMULATION_TIME_OPTIONS.filter((item) => item.flowMode === flowMode)
    assert.deepEqual(options.map((item) => item.label), labels)
    assert.deepEqual(options.map((item) => item.windowStartSeconds), [0, 900, 1800, 2700, 3600, 4500, 5400, 6300])
    assert.ok(options.every((item) => item.durationSeconds === 900))
  }
})

test('keeps the final evening window aligned to the backend offset contract', () => {
  const option = SIMULATION_TIME_OPTIONS.find((item) => item.value === 'evening_1900')
  assert.ok(option)
  assert.equal(option.flowMode, 'evening_peak')
  assert.equal(option.windowStartSeconds, 5400)
  assert.equal(option.durationSeconds, 900)
})
