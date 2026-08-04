import assert from 'node:assert/strict'
import test from 'node:test'

import { buildRoadJoints } from '../src/mapv/realistic/intersectionRoadJoints.ts'

function road(id, points, width = 8) {
  return {
    id,
    incoming: false,
    incident: false,
    centerline: points,
    roadWidth: width,
    lanes: [{
      id: `${id}_0`,
      index: 0,
      kind: 'driving',
      width,
      speed: 13.9,
      points,
    }],
  }
}

test('builds an overlapping tapered asphalt bridge for a connected road pair', () => {
  const joints = buildRoadJoints({
    edges: [road('west', [[-20, 0], [-4, 0]], 8), road('east', [[4, 0], [20, 0]], 10)],
    endpoints: [
      { edgeId: 'west', junctionId: 'secondary', endpoint: 'end' },
      { edgeId: 'east', junctionId: 'secondary', endpoint: 'start' },
    ],
    connections: [{ junctionId: 'secondary', fromEdge: 'west', toEdge: 'east' }],
    primaryJunctionId: 'primary',
    primaryJunctionShape: [[-1, -1], [1, -1], [1, 1], [-1, 1]],
    horizontalScale: 1,
  })
  assert.equal(joints.length, 1)
  assert.equal(joints[0].kind, 'continuation')
  assert.deepEqual(joints[0].connectedEdgeIds, ['east', 'west'])
  assert.equal(joints[0].overlapMeters, 0.5)
  assert.ok(joints[0].polygons.asphalt.some(([x]) => x < -4))
  assert.ok(joints[0].polygons.asphalt.some(([x]) => x > 4))
})

test('uses the primary SUMO junction boundary to seal a multi-arm intersection', () => {
  const joints = buildRoadJoints({
    edges: [
      road('west', [[-20, 0], [-4, 0]]),
      road('east', [[4, 0], [20, 0]]),
      road('north', [[0, 20], [0, 4]]),
    ],
    endpoints: [
      { edgeId: 'west', junctionId: 'primary', endpoint: 'end' },
      { edgeId: 'east', junctionId: 'primary', endpoint: 'start' },
      { edgeId: 'north', junctionId: 'primary', endpoint: 'end' },
    ],
    connections: [
      { junctionId: 'primary', fromEdge: 'west', toEdge: 'east' },
      { junctionId: 'primary', fromEdge: 'north', toEdge: 'east' },
    ],
    primaryJunctionId: 'primary',
    primaryJunctionShape: [[-3, -3], [3, -3], [3, 3], [-3, 3]],
    horizontalScale: 1,
  })
  assert.equal(joints.length, 1)
  assert.equal(joints[0].kind, 'junction')
  assert.equal(joints[0].connectedEdgeIds.length, 3)
  assert.ok(joints[0].polygons.asphalt.length >= 4)
})

test('does not invent a secondary road when topology is absent or the gap exceeds 20 m', () => {
  const edges = [road('left', [[-40, 0], [-15, 0]]), road('right', [[15, 0], [40, 0]])]
  const endpoints = [
    { edgeId: 'left', junctionId: 'secondary', endpoint: 'end' },
    { edgeId: 'right', junctionId: 'secondary', endpoint: 'start' },
  ]
  const common = {
    edges,
    endpoints,
    primaryJunctionId: 'primary',
    primaryJunctionShape: [[-1, -1], [1, -1], [1, 1], [-1, 1]],
    horizontalScale: 1,
  }
  assert.deepEqual(buildRoadJoints({ ...common, connections: [] }), [])
  assert.deepEqual(buildRoadJoints({
    ...common,
    connections: [{ junctionId: 'secondary', fromEdge: 'left', toEdge: 'right' }],
  }), [])
})
