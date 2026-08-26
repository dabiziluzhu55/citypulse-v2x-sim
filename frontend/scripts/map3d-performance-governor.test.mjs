import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAP3D_NORMAL_FRAME_RATE,
  MAP3D_STABLE_FRAME_RATE,
  Map3dPerformanceGovernor,
} from '../src/mapv/map3dPerformanceGovernor.ts'

test('uses a 45 fps normal budget and a 30 fps latched stable budget', () => {
  assert.equal(MAP3D_NORMAL_FRAME_RATE, 45)
  assert.equal(MAP3D_STABLE_FRAME_RATE, 30)
  const governor = new Map3dPerformanceGovernor()
  let degraded = false
  for (let now = 0; now <= 6_000; now += 1_000 / 30) degraded ||= governor.recordFrame(now)
  assert.equal(degraded, true)
  assert.equal(governor.stats().stableMode, true)
})

test('latches after three main-thread tasks over 100 ms and never oscillates', () => {
  const governor = new Map3dPerformanceGovernor()
  assert.equal(governor.recordLongTask(101, 1_000), false)
  assert.equal(governor.recordLongTask(130, 2_000), false)
  assert.equal(governor.recordLongTask(180, 3_000), true)
  assert.equal(governor.recordLongTask(200, 70_000), false)
  assert.equal(governor.stats().stableMode, true)
})

test('ignores short tasks and keeps the default presentation budget', () => {
  const governor = new Map3dPerformanceGovernor()
  for (let now = 0; now <= 10_000; now += 16) governor.recordFrame(now)
  governor.recordLongTask(80, 10_000)
  assert.equal(governor.stats().stableMode, false)
})
