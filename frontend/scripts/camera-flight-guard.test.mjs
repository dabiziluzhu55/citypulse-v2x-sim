import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  CAMERA_FLIGHT_WATCHDOG_MINIMUM_MS,
  cameraFlightWatchdogDelay,
  createCameraFlightGuard,
} from '../src/utils/cameraFlightGuard.ts'

test('camera flight watchdog includes grace time and a minimum duration', () => {
  assert.equal(cameraFlightWatchdogDelay(0), CAMERA_FLIGHT_WATCHDOG_MINIMUM_MS)
  assert.equal(cameraFlightWatchdogDelay(900), 2_100)
  assert.equal(cameraFlightWatchdogDelay(Number.NaN), CAMERA_FLIGHT_WATCHDOG_MINIMUM_MS)
})

test('camera flight guard completes exactly once on a normal callback', async () => {
  let completions = 0
  let timeouts = 0
  const guard = createCameraFlightGuard({
    timeoutMs: 10,
    onComplete: () => { completions += 1 },
    onTimeout: () => { timeouts += 1 },
  })

  guard.complete()
  guard.complete()
  await new Promise((resolve) => setTimeout(resolve, 20))

  assert.equal(completions, 1)
  assert.equal(timeouts, 0)
})

test('camera flight guard snaps and completes after a missing callback', async () => {
  const events = []
  createCameraFlightGuard({
    timeoutMs: 5,
    onComplete: () => events.push('complete'),
    onTimeout: () => events.push('timeout'),
  })

  await new Promise((resolve) => setTimeout(resolve, 15))
  assert.deepEqual(events, ['timeout', 'complete'])
})

test('cancelled camera flight guard cannot complete a stale transaction', async () => {
  let completions = 0
  const guard = createCameraFlightGuard({
    timeoutMs: 5,
    onComplete: () => { completions += 1 },
    onTimeout: () => { throw new Error('cancelled guard timed out') },
  })

  guard.cancel()
  guard.complete()
  await new Promise((resolve) => setTimeout(resolve, 15))
  assert.equal(completions, 0)
})

test('optional intersection environment cannot block realistic road activation', async () => {
  const source = await readFile(
    new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
    'utf8',
  )
  const activateAt = source.indexOf('realisticIntersectionLayer.activate(intersectionId)')
  const readyAt = source.indexOf('resourcesReady = true', activateAt)
  const optionalEnvironmentAt = source.indexOf(
    'void switchIntersectionEnvironment(intersectionId, revision).catch',
    readyAt,
  )

  assert.ok(activateAt >= 0)
  assert.ok(readyAt > activateAt)
  assert.ok(optionalEnvironmentAt > readyAt)
  assert.doesNotMatch(source, /await switchIntersectionEnvironment\(intersectionId, revision\)/)
})
