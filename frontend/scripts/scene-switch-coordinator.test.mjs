import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { SceneSwitchCoordinator } from '../src/utils/sceneSwitchCoordinator.ts'

const threeMapSource = await readFile(
  new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
  'utf8',
)
const vehicleRendererSource = await readFile(
  new URL('../src/mapv/BaiduVehicleRenderer.ts', import.meta.url),
  'utf8',
)

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

test('prepares a vehicle stage before camera flight and primes Twin before road activation', () => {
  const prepareIndex = threeMapSource.indexOf('waitForViewportVehicleStage(')
  const beginIndex = threeMapSource.indexOf('vehicleRenderer?.beginViewportTransition()', prepareIndex)
  const commitIndex = threeMapSource.indexOf('vehicleRenderer?.commitViewportTransition(', beginIndex)
  const activateIndex = threeMapSource.indexOf('realisticIntersectionLayer.activate(intersectionId)', commitIndex)
  assert.ok(prepareIndex >= 0 && prepareIndex < beginIndex)
  assert.ok(beginIndex < commitIndex && commitIndex < activateIndex)
  assert.match(vehicleRendererSource, /VIEWPORT_SNAPSHOT_HISTORY_SECONDS = 9/)
  assert.match(vehicleRendererSource, /selectViewportReplayVehicles/)
  assert.match(vehicleRendererSource, /stage\.authoritativeLocalVehicleCount > 0/)
  assert.match(vehicleRendererSource, /this\.twin\.reset\(\)[\s\S]*this\.presentImmediate\(primingSamples\)/)
})
