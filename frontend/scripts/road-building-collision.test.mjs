import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  findRoadSurfaceExclusions,
  MANUAL_VISUAL_OVERRIDE_AREAS,
} from './audit-road-building-collisions.mjs'
import {
  normalizedSurfaceExclusions,
  surfaceOffsetIsExcluded,
  visiblePolylineSections,
} from '../src/mapv/realistic/roadSurfaceExclusions.ts'

function edge(overrides = {}) {
  const points = Array.from({ length: 11 }, (_, index) => [index * 10, 0])
  return {
    id: 'road-a',
    incident: false,
    incoming: false,
    centerline: points,
    roadWidth: 10,
    lanes: [
      { id: 'road-a_0', index: 0, width: 5, speed: 10, renderPoints: points.map(([x]) => [x, -2.5]), points },
      { id: 'road-a_1', index: 1, width: 5, speed: 10, renderPoints: points.map(([x]) => [x, 2.5]), points },
    ],
    ...overrides,
  }
}

function manifest(road) {
  return {
    horizontalScale: 1,
    junctionShape: [[-5, -5], [5, -5], [5, 5], [-5, 5]],
    roadJoints: [],
    edges: [road],
  }
}

function triangle(points, tile = 'tile_roof_test.glb') {
  return {
    points,
    tile,
    bounds: {
      minX: Math.min(...points.map((point) => point[0])),
      maxX: Math.max(...points.map((point) => point[0])),
      minY: Math.min(...points.map((point) => point[1])),
      maxY: Math.max(...points.map((point) => point[1])),
    },
  }
}

test('classifies only non-core outer-surface building overlaps as exclusions', () => {
  const road = edge()
  const outerOnly = triangle([[70, 7], [80, 7], [75, 9]])
  const result = findRoadSurfaceExclusions(manifest(road), [outerOnly])
  assert.equal(result.centerlineConflicts.length, 0)
  assert.equal(result.edges.length, 1)
  assert.equal(result.edges[0].edgeId, 'road-a')
  assert.ok(result.edges[0].surfaceExclusions[0].startOffsetMeters > 50)
  assert.ok(result.edges[0].surfaceExclusions[0].endOffsetMeters < 92)
})

test('protects incident roads, lane centerlines, and road-joint buffers', () => {
  const outerOnly = triangle([[70, 7], [80, 7], [75, 9]])
  assert.equal(findRoadSurfaceExclusions(manifest(edge({ incident: true })), [outerOnly]).edges.length, 0)

  const centerline = triangle([[70, -1], [80, -1], [75, 3]])
  const centerlineResult = findRoadSurfaceExclusions(manifest(edge()), [centerline])
  assert.equal(centerlineResult.edges.length, 0)
  assert.ok(centerlineResult.centerlineConflicts.length > 0)

  const roadJoint = {
    jointId: 'joint-a', junctionId: 'secondary', kind: 'junction', connectedEdgeIds: ['road-a', 'road-b'],
    maxGapMeters: 0, overlapMeters: 0, source: 'sumo_junction_shape',
    polygons: {
      asphalt: [[68, 4], [82, 4], [82, 10], [68, 10]],
      curb: [[68, 4], [82, 4], [82, 10], [68, 10]],
      sidewalk: [[68, 4], [82, 4], [82, 10], [68, 10]],
    },
  }
  const protectedResult = findRoadSurfaceExclusions({ ...manifest(edge()), roadJoints: [roadJoint] }, [outerOnly])
  assert.equal(protectedResult.edges.length, 0)
})

test('splits every rendered polyline using the same edge-relative meter intervals', () => {
  const exclusions = [{
    startOffsetMeters: 30,
    endOffsetMeters: 60,
    reason: 'building_overlap',
    source: 'building_triangle_audit',
  }]
  assert.deepEqual(normalizedSurfaceExclusions(exclusions, 100), [[30, 60]])
  assert.equal(surfaceOffsetIsExcluded(45, exclusions, 100), true)
  assert.equal(surfaceOffsetIsExcluded(75, exclusions, 100), false)
  assert.deepEqual(visiblePolylineSections([[0, 0], [100, 0]], exclusions), [
    [[0, 0], [30, 0]],
    [[60, 0], [100, 0]],
  ])
  assert.deepEqual(visiblePolylineSections(
    [[0, 5], [200, 5]],
    exclusions,
    1,
    [[0, 0], [100, 0]],
  ), [
    [[0, 5], [60, 5]],
    [[120, 5], [200, 5]],
  ])
})

test('removes audited centerline conflicts only beyond the 200 metre far-field boundary', () => {
  const points = Array.from({ length: 41 }, (_, index) => [index * 10, 0])
  const road = edge({
    centerline: points,
    lanes: [{ id: 'road-a_0', index: 0, width: 10, speed: 10, renderPoints: points, points }],
  })
  const inside = triangle([[145, -2], [155, -2], [150, 2]], 'inside.glb')
  const outside = triangle([[245, -2], [255, -2], [250, 2]], 'outside.glb')
  const result = findRoadSurfaceExclusions(manifest(road), [inside, outside], {
    allowFarFieldCenterlineConflicts: true,
    farFieldRadiusMeters: 200,
  })
  assert.equal(result.edges.length, 1)
  assert.ok(result.edges[0].surfaceExclusions.every((range) => range.startOffsetMeters >= 200))
  assert.ok(result.edges[0].surfaceExclusions.some((range) => (
    range.startOffsetMeters <= 250 && range.endOffsetMeters >= 250
  )))
  assert.ok(result.edges[0].surfaceExclusions.every((range) => range.reason === 'building_overlap_far_field'))
})

test('keeps audited exclusions outside the core and scopes manual visual overrides to reviewed assets', async () => {
  const demo5ManualEdgeIds = new Set(MANUAL_VISUAL_OVERRIDE_AREAS.demo_5.flatMap(
    (area) => Object.keys(area.edges),
  ))
  for (const index of [5, 6, 9]) {
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/demo_${index}/manifest.json`, import.meta.url),
      'utf8',
    ))
    const scale = manifest.horizontalScale ?? 1
    for (const edge of manifest.edges) {
      for (const exclusion of edge.surfaceExclusions ?? []) {
        assert.equal(edge.incident, false)
        if (exclusion.reason === 'building_overlap_visual_override') {
          assert.ok(index === 5 || index === 6)
          assert.ok(index === 5
            ? demo5ManualEdgeIds.has(edge.id)
            : ['-50357', '-50358', '-56639', '-56640'].includes(edge.id))
          assert.equal(exclusion.source, 'manual_visual_review')
          if (index === 6) assert.equal(exclusion.startOffsetMeters, 0)
          continue
        }
        assert.equal(exclusion.reason, 'building_overlap_far_field')
        assert.equal(exclusion.source, 'building_triangle_audit')
        const points = edge.centerline
        const { cumulative, total } = (() => {
          const values = [0]
          for (let pointIndex = 1; pointIndex < points.length; pointIndex += 1) {
            values.push(values.at(-1) + Math.hypot(
              points[pointIndex][0] - points[pointIndex - 1][0],
              points[pointIndex][1] - points[pointIndex - 1][1],
            ))
          }
          return { cumulative: values, total: values.at(-1) }
        })()
        const sample = (offsetMeters) => {
          const target = Math.max(0, Math.min(total, offsetMeters * scale))
          let pointIndex = 1
          while (pointIndex < cumulative.length - 1 && cumulative[pointIndex] < target) pointIndex += 1
          const segment = cumulative[pointIndex] - cumulative[pointIndex - 1]
          const ratio = segment > 0 ? (target - cumulative[pointIndex - 1]) / segment : 0
          return [
            points[pointIndex - 1][0] + (points[pointIndex][0] - points[pointIndex - 1][0]) * ratio,
            points[pointIndex - 1][1] + (points[pointIndex][1] - points[pointIndex - 1][1]) * ratio,
          ]
        }
        for (const offset of [
          exclusion.startOffsetMeters,
          (exclusion.startOffsetMeters + exclusion.endOffsetMeters) / 2,
          exclusion.endOffsetMeters,
        ]) {
          const point = sample(offset)
          assert.ok(Math.hypot(...point) / scale >= 199.9, `demo_${index}:${edge.id} enters the 200 m core`)
        }
      }
    }
  }
})

test('fully suppresses the two manually reviewed demo_6 corridors and their continuation joints', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_6/manifest.json', import.meta.url),
    'utf8',
  ))
  const expectedLengths = new Map([
    ['-50357', 306.24],
    ['-56639', 306.105],
    ['-50358', 389.836],
    ['-56640', 389.573],
  ])
  for (const [edgeId, expectedLength] of expectedLengths) {
    const edge = manifest.edges.find((candidate) => candidate.id === edgeId)
    const manual = edge?.surfaceExclusions?.find((item) => (
      item.reason === 'building_overlap_visual_override'
    ))
    assert.ok(manual, `${edgeId} is missing its manual visual override`)
    assert.equal(manual.startOffsetMeters, 0)
    assert.equal(manual.endOffsetMeters, expectedLength)
  }
  for (const jointId of ['3954:1', '3955:1']) {
    const joint = manifest.roadJoints.find((candidate) => candidate.jointId === jointId)
    assert.deepEqual(joint?.surfaceHidden, {
      reason: 'building_overlap_visual_override',
      source: 'manual_visual_review',
    })
  }
})

test('removes exactly the six marked demo_5 conflicts without touching the primary intersection', async () => {
  const areas = MANUAL_VISUAL_OVERRIDE_AREAS.demo_5
  assert.equal(areas.length, 6)
  assert.equal(new Set(areas.map((area) => area.id)).size, 6)
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_5/manifest.json', import.meta.url),
    'utf8',
  ))
  const expectedByEdge = new Map()
  for (const area of areas) {
    assert.equal(Object.keys(area.edges).length, 2)
    for (const [edgeId, [startOffsetMeters, endOffsetMeters]] of Object.entries(area.edges)) {
      const expected = expectedByEdge.get(edgeId) ?? []
      expected.push({
        startOffsetMeters,
        endOffsetMeters,
        reason: 'building_overlap_visual_override',
        source: 'manual_visual_review',
      })
      expectedByEdge.set(edgeId, expected)
    }
  }
  for (const edge of manifest.edges) {
    const manual = (edge.surfaceExclusions ?? []).filter((item) => item.source === 'manual_visual_review')
    assert.deepEqual(manual, expectedByEdge.get(edge.id) ?? [], edge.id)
    if (manual.length > 0) assert.equal(edge.incident, false)
  }
  const expectedJointIds = areas.flatMap((area) => area.jointId ? [area.jointId] : []).sort()
  const hiddenJointIds = manifest.roadJoints
    .filter((joint) => joint.surfaceHidden?.source === 'manual_visual_review')
    .map((joint) => joint.jointId)
    .sort()
  assert.deepEqual(hiddenJointIds, expectedJointIds)
  assert.equal(
    manifest.roadJoints.find((joint) => joint.junctionId === manifest.junctionId)?.surfaceHidden,
    undefined,
  )
})
