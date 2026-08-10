import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  localRoadElevationBlend,
  topologyFlowHeight,
} from '../src/mapv/topologyFlowElevation.ts'

const node = {
  intersectionId: 'demo_1',
  longitude: 116,
  latitude: 39,
  radiusMeters: 520,
}

function northByMeters(meters) {
  return [node.longitude, node.latitude + meters / 110_900]
}

test('keeps flow light above both Baidu and local realistic road surfaces', () => {
  assert.ok(Math.abs(topologyFlowHeight([116, 39], [node], 'base') - 1.18) < 1e-9)
  assert.ok(Math.abs(topologyFlowHeight([116, 39], [node], 'flow') - 1.24) < 1e-9)
  assert.ok(Math.abs(topologyFlowHeight(northByMeters(700), [node], 'base') - 0.39) < 0.01)
  assert.ok(Math.abs(topologyFlowHeight(northByMeters(700), [node], 'flow') - 0.45) < 0.01)
})

test('smoothly lowers the light over the final twenty meters of a local road patch', () => {
  const inside = localRoadElevationBlend(northByMeters(499), [node])
  const middle = localRoadElevationBlend(northByMeters(510), [node])
  const outside = localRoadElevationBlend(northByMeters(521), [node])
  assert.ok(inside > 0.99)
  assert.ok(middle > 0.35 && middle < 0.65)
  assert.equal(outside, 0)
  assert.ok(topologyFlowHeight(northByMeters(499), [node], 'flow') > topologyFlowHeight(northByMeters(510), [node], 'flow'))
  assert.ok(topologyFlowHeight(northByMeters(510), [node], 'flow') > topologyFlowHeight(northByMeters(521), [node], 'flow'))
})

test('keeps depth occlusion while drawing the two light layers after road markings', async () => {
  const source = await readFile(
    new URL('../src/mapv/IntersectionTopologyLayer.ts', import.meta.url),
    'utf8',
  )
  assert.match(source, /owner\.material\.depthTest = true/)
  assert.match(source, /owner\.material\.depthWrite = false/)
  assert.match(source, /this\.baseLine\.renderOrder = 34/)
  assert.match(source, /this\.flowLine\.renderOrder = 35/)
  assert.doesNotMatch(source, /projector\(\[longitude, latitude, 0\.32\]\)/)
})
