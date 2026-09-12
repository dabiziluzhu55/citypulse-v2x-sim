import test from 'node:test'
import assert from 'node:assert/strict'
import { captureMapView } from '../src/utils/mapViewCheckpoint.ts'

test('rollback restores the committed camera and reapplies it without sharing mutable coordinates', () => {
  const view = {
    mode: { value: 'anchored' }, cameraPreset: { value: 'birdseye' },
    anchorId: { value: 'intersection:demo_2' },
    viewport: { value: { kind: 'center', center: [116, 39], zoom: 18 } },
  }
  let applications = 0
  const restore = captureMapView(view, () => { applications++ })
  view.viewport.value.center[0] = 120
  view.anchorId.value = 'intersection:demo_3'
  view.cameraPreset.value = 'overview'
  restore()
  assert.deepEqual(view.viewport.value.center, [116, 39])
  assert.equal(view.anchorId.value, 'intersection:demo_2')
  assert.equal(view.cameraPreset.value, 'birdseye')
  view.viewport.value.center[0] = 121
  restore()
  assert.deepEqual(view.viewport.value.center, [116, 39])
  assert.equal(applications, 2)
})
