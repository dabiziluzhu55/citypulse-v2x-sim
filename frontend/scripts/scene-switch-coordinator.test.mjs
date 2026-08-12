import assert from 'node:assert/strict'
import test from 'node:test'

import { SceneSwitchCoordinator } from '../src/utils/sceneSwitchCoordinator.ts'

test('a newer scene switch cancels stale work and is the only committable transaction', () => {
  const coordinator = new SceneSwitchCoordinator()
  const first = coordinator.begin('demo_4')
  const second = coordinator.begin('demo_8')

  assert.equal(first.signal.aborted, true)
  assert.equal(coordinator.isCurrent(first), false)
  assert.equal(coordinator.complete(first), false)
  assert.equal(coordinator.isCurrent(second), true)
  assert.equal(coordinator.complete(second), true)
})

test('cancelling a switch prevents any later commit', () => {
  const coordinator = new SceneSwitchCoordinator()
  const transaction = coordinator.begin('demo_6')
  coordinator.cancel()

  assert.equal(transaction.signal.aborted, true)
  assert.equal(coordinator.complete(transaction), false)
})
