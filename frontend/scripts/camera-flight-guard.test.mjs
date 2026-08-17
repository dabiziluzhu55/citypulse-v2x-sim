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

test('freezes Twin playback for a programmed camera flight and resumes before viewport refresh', async () => {
  const [mapSource, rendererSource] = await Promise.all([
    readFile(
      new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
      'utf8',
    ),
    readFile(
      new URL('../src/mapv/BaiduVehicleRenderer.ts', import.meta.url),
      'utf8',
    ),
  ])

  const flightStartAt = mapSource.indexOf('cameraFlightActive = options.duration > 0')
  const holdAt = mapSource.indexOf(
    'vehicleRenderer?.setCameraTransitionActive(cameraFlightActive)',
    flightStartAt,
  )
  const flyAt = mapSource.indexOf('engine?.map.flyTo(placedTarget', holdAt)
  const finishAt = mapSource.indexOf('const finishFlight = () =>', holdAt)
  const releaseAt = mapSource.indexOf(
    'vehicleRenderer?.setCameraTransitionActive(false)',
    finishAt,
  )
  const refreshAt = mapSource.indexOf(
    'refreshVehicleViewportAfterCameraPlacement()',
    releaseAt,
  )

  assert.ok(flightStartAt >= 0)
  assert.ok(holdAt > flightStartAt && holdAt < flyAt)
  assert.ok(releaseAt > finishAt && releaseAt < refreshAt)
  assert.match(rendererSource, /!this\.active\s*\|\| this\.cameraTransitionHeld/)
  assert.match(rendererSource, /this\.twinPresenter\.freezeAfterVisible\(\)/)
  assert.match(rendererSource, /!this\.cameraTransitionHeld\s*&& isVehicleAnimationActive/)
  assert.match(mapSource, /VEHICLE_TWIN_VIEWPORT_WARMUP_TIMEOUT_MS = 8_000/)
  assert.match(
    mapSource,
    /waitForViewportTransitionReady\(signal, VEHICLE_TWIN_VIEWPORT_WARMUP_TIMEOUT_MS\)/,
  )
})

test('initial presentation waits for facilities while optional environment layers fail safely', async () => {
  const source = await readFile(
    new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
    'utf8',
  )
  const environmentPrepareAt = source.indexOf(
    'prepareIntersectionEnvironment(intersectionId, signal)',
  )
  const activateAt = source.indexOf(
    'realisticIntersectionLayer.activate(intersectionId)',
    environmentPrepareAt,
  )
  const environmentCommitAt = source.indexOf(
    'commitIntersectionEnvironment(intersectionId, preparedEnvironment, revision)',
    activateAt,
  )
  const environmentReadyAt = source.indexOf('initialEnvironmentReady = true', environmentCommitAt)
  const landcoverPrepareAt = source.indexOf(
    'ensureIntersectionLandcover(intersectionId, environment, signal)',
  )
  const optionalLandcoverCatchAt = source.indexOf('.catch((cause: unknown)', landcoverPrepareAt)

  assert.ok(environmentPrepareAt >= 0)
  assert.ok(activateAt > environmentPrepareAt)
  assert.ok(environmentCommitAt > activateAt)
  assert.ok(environmentReadyAt > environmentCommitAt)
  assert.ok(optionalLandcoverCatchAt > landcoverPrepareAt)
})
