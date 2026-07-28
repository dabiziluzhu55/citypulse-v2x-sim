import assert from 'node:assert/strict'
import test from 'node:test'

import { useActiveIntersectionScene } from '../src/composables/useActiveIntersectionScene.ts'

test('reselecting the same intersection creates a new focus revision', () => {
  const scene = useActiveIntersectionScene()
  scene.selectIntersection('demo_2')
  const first = scene.selectionRevision.value
  scene.selectIntersection('demo_2')

  assert.equal(scene.activeIntersectionId.value, 'demo_2')
  assert.equal(scene.selectionRevision.value, first + 1)
})

test('tracks the scene that was atomically committed', () => {
  const scene = useActiveIntersectionScene()
  scene.selectIntersection('demo_3')
  scene.setSceneReady('demo_3')

  assert.equal(scene.committedIntersectionId.value, 'demo_3')
  assert.equal(scene.sceneStatus.value, 'ready')
})
