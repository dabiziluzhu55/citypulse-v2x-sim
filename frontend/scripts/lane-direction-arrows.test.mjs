import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { DoubleSide, MeshBasicMaterial } from 'three'

import {
  LANE_ARROW_MAX_LANE_WIDTH_RATIO,
  LANE_ARROW_MAX_VISIBLE_RANGE_METERS,
  LANE_ARROW_RENDER_ORDER,
  LANE_ARROW_SAMPLE_DISTANCE_METERS,
  LANE_ARROW_SURFACE_Z,
  aggregateLaneMovements,
  auditLaneDirectionArrows,
  createLaneArrowGeometry,
  createLaneArrowMaterial,
  laneArrowPattern,
  laneArrowsAvailableForLod,
} from '../src/mapv/realistic/laneDirectionArrows.ts'
import { visualLanePoints } from '../src/mapv/realistic/intersectionRoadGeometry.ts'

const manifests = await Promise.all(Array.from({ length: 20 }, async (_, index) => (
  JSON.parse(await readFile(
    new URL(`../public/intersections/v3/demo_${index + 1}/manifest.json`, import.meta.url),
    'utf8',
  ))
)))

function pointToPolylineDistance(point, points) {
  return Math.min(...points.slice(1).map((end, index) => {
    const start = points[index]
    const dx = end[0] - start[0]
    const dy = end[1] - start[1]
    const denominator = dx * dx + dy * dy
    const ratio = denominator === 0 ? 0 : Math.max(0, Math.min(1,
      ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator,
    ))
    return Math.hypot(
      point[0] - start[0] - dx * ratio,
      point[1] - start[1] - dy * ratio,
    )
  }))
}

test('supports all seven current combinations and standalone U-turns without fallback', () => {
  const expected = ['l', 's', 'r', 'l+s', 's+r', 'l+r', 'l+s+r', 't']
  for (const pattern of expected) {
    assert.equal(laneArrowPattern(pattern.split('+')), pattern)
    const geometry = createLaneArrowGeometry(pattern)
    assert.ok(geometry.getAttribute('position').count > 0)
    assert.ok(geometry.boundingBox)
    geometry.dispose()
  }
  assert.equal(laneArrowPattern(['t', 's']), null)
  assert.equal(laneArrowPattern([]), null)
})

test('aggregates all SUMO movements once for each controlled approach lane', () => {
  const manifest = manifests[0]
  const grouped = aggregateLaneMovements(manifest.connections)
  assert.ok(grouped.size > 0)
  for (const [key, group] of grouped) {
    const source = manifest.connections.filter((connection) => (
      `${connection.fromEdge}:${connection.fromLane}` === key
    ))
    assert.deepEqual(
      new Set(group.movements),
      new Set(source.map((connection) => connection.direction)),
    )
  }
})

test('audits the fixed twenty-intersection arrow contract', () => {
  let controlledLaneCount = 0
  let multiMovementLaneCount = 0
  let bicycleClassificationCount = 0
  const patterns = new Map()

  for (const manifest of manifests) {
    const audit = auditLaneDirectionArrows(manifest)
    assert.deepEqual(audit.unsupported, [], manifest.intersectionId)
    assert.equal(audit.arrows.length, audit.controlledLaneCount, manifest.intersectionId)
    assert.equal(new Set(audit.arrows.map((arrow) => arrow.key)).size, audit.arrows.length)
    controlledLaneCount += audit.controlledLaneCount
    multiMovementLaneCount += audit.multiMovementLaneCount
    bicycleClassificationCount += audit.warnings.length
    for (const arrow of audit.arrows) {
      patterns.set(arrow.pattern, (patterns.get(arrow.pattern) ?? 0) + 1)
    }
  }

  assert.equal(controlledLaneCount, 156)
  assert.equal(multiMovementLaneCount, 97)
  assert.equal(bicycleClassificationCount, 29)
  assert.deepEqual(Object.fromEntries([...patterns].sort()), {
    l: 16,
    'l+r': 5,
    'l+s': 29,
    'l+s+r': 18,
    r: 9,
    s: 34,
    's+r': 45,
  })
})

test('places every arrow on its real lane with a bounded width and heading', () => {
  for (const manifest of manifests) {
    const audit = auditLaneDirectionArrows(manifest)
    for (const arrow of audit.arrows) {
      const edge = manifest.edges.find((candidate) => candidate.id === arrow.edgeId)
      const lane = edge.lanes.find((candidate) => candidate.index === arrow.laneIndex)
      assert.notEqual(lane.kind, 'pedestrian')
      assert.ok(manifest.connections.some((connection) => (
        connection.fromEdge === arrow.edgeId && connection.fromLane === arrow.laneIndex
      )), arrow.key)
      assert.ok(pointToPolylineDistance(arrow.point, visualLanePoints(lane)) < 1e-6, arrow.key)
      assert.ok(arrow.sampleDistanceMeters > 0, arrow.key)
      assert.ok(arrow.sampleDistanceMeters <= LANE_ARROW_SAMPLE_DISTANCE_METERS + 1e-6, arrow.key)

      const direction = [-Math.sin(arrow.headingRadians), Math.cos(arrow.headingRadians)]
      const dot = Math.max(-1, Math.min(1,
        direction[0] * arrow.tangent[0] + direction[1] * arrow.tangent[1],
      ))
      const headingErrorDegrees = Math.acos(dot) * 180 / Math.PI
      assert.ok(headingErrorDegrees <= 3, `${arrow.key}: ${headingErrorDegrees}`)

      const geometry = createLaneArrowGeometry(arrow.pattern)
      const width = (geometry.boundingBox.max.x - geometry.boundingBox.min.x) * arrow.scale
      assert.ok(width <= arrow.laneWidth * LANE_ARROW_MAX_LANE_WIDTH_RATIO + 1e-6, arrow.key)
      geometry.dispose()
    }
  }
})

test('uses stable unlit road-surface material and medium/full LOD only', () => {
  const material = createLaneArrowMaterial()
  assert.ok(material instanceof MeshBasicMaterial)
  assert.equal(material.depthTest, true)
  assert.equal(material.depthWrite, false)
  assert.equal(material.toneMapped, false)
  assert.equal(material.side, DoubleSide)
  assert.equal(material.polygonOffset, true)
  assert.ok(material.polygonOffsetFactor < 0)
  assert.ok(LANE_ARROW_SURFACE_Z > 0.055)
  assert.equal(LANE_ARROW_RENDER_ORDER, 33)
  assert.equal(LANE_ARROW_MAX_VISIBLE_RANGE_METERS, 6_000)
  assert.equal(laneArrowsAvailableForLod('overview'), false)
  assert.equal(laneArrowsAvailableForLod('medium'), true)
  assert.equal(laneArrowsAvailableForLod('full'), true)
  material.dispose()
})
