import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canPauseSimulation,
  canResumeSimulation,
  isActiveSimulationState,
  shouldAutoPresentSimulation,
  simulationStateLabel,
} from '../src/utils/simulationSessionState.ts'
import { ApiError, simulationApiErrorMessage } from '../src/api/client.ts'

test('keeps queued sessions active but does not auto-open 3D before a worker starts', () => {
  assert.equal(isActiveSimulationState('QUEUED'), true)
  assert.equal(shouldAutoPresentSimulation('QUEUED'), false)
  assert.equal(shouldAutoPresentSimulation('STARTING'), true)
  assert.equal(shouldAutoPresentSimulation('RUNNING'), true)
  assert.equal(simulationStateLabel('QUEUED'), '排队中')
})

test('disables playback controls while queued and preserves stop as an active-session action', () => {
  assert.equal(canPauseSimulation('QUEUED'), false)
  assert.equal(canResumeSimulation('QUEUED'), false)
  assert.equal(canPauseSimulation('RUNNING'), true)
  assert.equal(canResumeSimulation('PAUSED'), true)
})

test('presents Redis and queued business errors without discarding their codes', () => {
  const queued = new ApiError({ status: 409, code: 'SESSION_QUEUED', message: 'queued' })
  assert.equal(queued.status, 409)
  assert.equal(queued.code, 'SESSION_QUEUED')
  assert.equal(simulationApiErrorMessage(queued, 'fallback'), '排队期间暂不可执行该操作')
  const redis = new ApiError({ status: 503, code: 'REDIS_UNAVAILABLE', message: 'offline' })
  assert.equal(simulationApiErrorMessage(redis, 'fallback'), '仿真调度服务不可用，请稍后重试')
})
