import assert from 'node:assert/strict'
import test from 'node:test'

import { useActiveIntersectionScene } from '../src/composables/useActiveIntersectionScene.ts'
import { createIntersectionFocusTransaction } from '../src/utils/intersectionFocus.ts'

test('reselecting the same intersection does not restart the focus transaction', () => {
  const scene = useActiveIntersectionScene()
  scene.selectIntersection('demo_2')
  const first = scene.selectionRevision.value
  scene.selectIntersection('demo_2')

  assert.equal(scene.activeIntersectionId.value, 'demo_2')
  assert.equal(scene.selectionRevision.value, first)
})

test('tracks the scene that was atomically committed', () => {
  const scene = useActiveIntersectionScene()
  scene.selectIntersection('demo_3')
  scene.setSceneReady('demo_3')

  assert.equal(scene.committedIntersectionId.value, 'demo_3')
  assert.equal(scene.sceneStatus.value, 'ready')
})

test('camera preset changes keep the latest committed intersection center', () => {
  const stale = createIntersectionFocusTransaction([116.1267597, 38.9911472], 'demo_2')
  let completed = false
  const current = createIntersectionFocusTransaction([116.0702724, 38.9768003], 'demo_3', {
    force: true,
    duration: 0,
    complete: () => { completed = true },
  })
  current.applyOptions.complete?.()

  assert.equal(completed, true)
  assert.equal(current.anchorId, 'intersection:demo_3')
  assert.notDeepEqual(current.viewport.center, stale.viewport.center)
  assert.deepEqual(current.viewport.center, [116.0702724, 38.9768003])
  assert.equal(current.applyOptions.duration, 0)
})
