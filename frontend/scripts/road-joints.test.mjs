import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { buildRoadJoints } from '../src/mapv/realistic/intersectionRoadJoints.ts'

function pointSegmentDistance(point, start, end) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const lengthSquared = dx * dx + dy * dy
  const ratio = lengthSquared > 0
    ? Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared))
    : 0
  return Math.hypot(point[0] - start[0] - dx * ratio, point[1] - start[1] - dy * ratio)
}

function pointInPolygon(point, polygon) {
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const a = polygon[previous]
    const b = polygon[index]
    if ((a[1] > point[1]) === (b[1] > point[1])) continue
    const x = (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]
    if (point[0] < x) inside = !inside
  }
  return inside
}

function distanceToSurface(point, parts) {
  let minimum = Number.POSITIVE_INFINITY
  for (const part of parts) {
    if (pointInPolygon(point, part.outer) && !(part.holes ?? []).some((hole) => pointInPolygon(point, hole))) return 0
    for (let index = 0; index < part.outer.length; index += 1) {
      minimum = Math.min(minimum, pointSegmentDistance(
        point,
        part.outer[index],
        part.outer[(index + 1) % part.outer.length],
      ))
    }
  }
  return minimum
}

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
  assert.ok(joints[0].surfaceParts.asphalt.length >= 1)
  assert.ok(joints[0].surfaceParts.curb.length >= 1)
  assert.ok(joints[0].surfaceParts.sidewalk.length >= 1)
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
  assert.ok(joints[0].surfaceParts.asphalt.every((part) => part.outer.length >= 4))
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

test('uses an authoritative SUMO junction shape for a connected long road gap', () => {
  const common = {
    edges: [road('left', [[-60, 0], [-30, 0]]), road('right', [[30, 0], [60, 0]])],
    endpoints: [
      { edgeId: 'left', junctionId: 'secondary', endpoint: 'end' },
      { edgeId: 'right', junctionId: 'secondary', endpoint: 'start' },
    ],
    connections: [{ junctionId: 'secondary', fromEdge: 'left', toEdge: 'right' }],
    primaryJunctionId: 'primary',
    primaryJunctionShape: [[-1, -1], [1, -1], [1, 1], [-1, 1]],
    horizontalScale: 1,
  }
  assert.deepEqual(buildRoadJoints({
    ...common,
    authoritativeJunctions: [{
      junctionId: 'secondary',
      shape: [[-30, -4], [30, -4], [30, 4], [-30, 4]],
      internalLaneIds: [],
    }],
  }), [])

  const joints = buildRoadJoints({
    ...common,
    authoritativeJunctions: [{
      junctionId: 'secondary',
      shape: [[-30, -4], [30, -4], [30, 4], [-30, 4]],
      internalLaneIds: [':secondary_0_0'],
    }],
  })
  assert.equal(joints.length, 1)
  assert.equal(joints[0].source, 'sumo_junction_shape')
  assert.equal(joints[0].kind, 'junction')
  assert.equal(joints[0].maxGapMeters, 60)
  assert.equal(joints[0].overlapMeters, 0.5)
  assert.ok(joints[0].polygons.asphalt.some(([x]) => x < -30))
  assert.ok(joints[0].polygons.asphalt.some(([x]) => x > 30))
})

test('demo_4 and demo_8 asphalt joints cover every connected road cap within 0.15 metres', async () => {
  for (const demo of [4, 8]) {
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/demo_${demo}/manifest.json`, import.meta.url),
      'utf8',
    ))
    for (const joint of manifest.roadJoints) {
      assert.ok(joint.surfaceParts?.asphalt?.length, `demo_${demo}:${joint.jointId} has no exact surface parts`)
      for (const edgeId of joint.connectedEdgeIds) {
        const edge = manifest.edges.find((candidate) => candidate.id === edgeId)
        const points = edge.centerline
        const candidates = [
          { point: points[0], adjacent: points[1] },
          { point: points.at(-1), adjacent: points.at(-2) },
        ]
        const endpoint = candidates.sort((left, right) => (
          distanceToSurface(left.point, joint.surfaceParts.asphalt)
          - distanceToSurface(right.point, joint.surfaceParts.asphalt)
        ))[0]
        const dx = endpoint.adjacent[0] - endpoint.point[0]
        const dy = endpoint.adjacent[1] - endpoint.point[1]
        const length = Math.hypot(dx, dy) || 1
        const normal = [-dy / length, dx / length]
        for (const sign of [-1, 1]) {
          const cap = [
            endpoint.point[0] + normal[0] * edge.roadWidth / 2 * sign,
            endpoint.point[1] + normal[1] * edge.roadWidth / 2 * sign,
          ]
          const gapMeters = distanceToSurface(cap, joint.surfaceParts.asphalt) / manifest.horizontalScale
          assert.ok(gapMeters <= 0.15, `demo_${demo}:${joint.jointId}:${edgeId} cap gap ${gapMeters}`)
        }
      }
    }
  }
})
