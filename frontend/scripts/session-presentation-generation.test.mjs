import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const storeSource = await readFile(
  new URL('../src/composables/useSimulationStore.ts', import.meta.url),
  'utf8',
)
const backgroundMapSource = await readFile(
  new URL('../src/components/visualization/AppBackgroundMap.vue', import.meta.url),
  'utf8',
)
const threeMapSource = await readFile(
  new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
  'utf8',
)

test('creates one synchronous presentation boundary after a run is accepted', () => {
  const bindBlock = storeSource.slice(
    storeSource.indexOf('function bindSession'),
    storeSource.indexOf('function ensureInitialized'),
  )
  assert.match(bindBlock, /const acceptedNewSession = Boolean\(nextSessionId && runtimePayload\)/)
  assert.ok(bindBlock.indexOf("connectSimulationStream('')") < bindBlock.indexOf('simulationPresentationGeneration.value += 1'))
  assert.ok(bindBlock.indexOf('snapshot.value = null') < bindBlock.indexOf('setRuntimeDisturbanceTargets(nextSessionId, runtimePayload)'))
  assert.ok(bindBlock.indexOf('clearPersistedRuntimeDisturbances()') < bindBlock.indexOf('setRuntimeDisturbanceTargets(nextSessionId, runtimePayload)'))
})

test('binds the new generation before auxiliary accepted-session callbacks', () => {
  const launchBlock = storeSource.slice(
    storeSource.indexOf('async function launchRun'),
    storeSource.indexOf('function clearStatusError'),
  )
  assert.ok(launchBlock.indexOf('bindSession(') < launchBlock.indexOf('onSessionAccepted?.(result)'))
})

test('tags both 2D and 3D event markers with the active presentation generation', () => {
  assert.match(backgroundMapSource, /sessionGeneration: simulationPresentationGeneration\.value/)
  assert.match(backgroundMapSource, /featureGeneration === simulationPresentationGeneration\.value/)
  assert.match(threeMapSource, /detected:\$\{simulationPresentationGeneration\.value\}/)
  assert.match(threeMapSource, /runtime:\$\{simulationPresentationGeneration\.value\}/)
})

test('rejects stale-session snapshots before mutating presentation state', () => {
  const applyBlock = storeSource.slice(
    storeSource.indexOf('function applySnapshot'),
    storeSource.indexOf('function stopPolling'),
  )
  assert.match(applyBlock, /next\.session_id !== sessionId\.value/)
  assert.match(applyBlock, /rejectedStaleSessionSnapshotCount\.value \+= 1/)
})
