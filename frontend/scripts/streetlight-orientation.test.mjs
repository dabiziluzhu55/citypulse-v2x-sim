import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const METERS_PER_DEGREE = 110_900
const MAX_HEADING_ERROR_RADIANS = 15 * Math.PI / 180
const LAMP_SPACING_METERS = 34
const LAMP_END_CLEARANCE_METERS = 18
const LAMP_ROADSIDE_CLEARANCE_METERS = 3.2

function normalize([x, y]) {
  const magnitude = Math.hypot(x, y) || 1
  return [x / magnitude, y / magnitude]
}

function distance(left, right) {
  return Math.hypot(left[0] - right[0], left[1] - right[1])
}

function dot(left, right) {
  return left[0] * right[0] + left[1] * right[1]
}

function pointAtProgress(points, progress) {
  const lengths = points.slice(1).map((point, index) => distance(points[index], point))
  const target = lengths.reduce((sum, value) => sum + value, 0) * progress
  let consumed = 0
  for (let index = 0; index < lengths.length; index += 1) {
    if (consumed + lengths[index] < target && index < lengths.length - 1) {
      consumed += lengths[index]
      continue
    }
    const ratio = lengths[index] > 1e-6 ? (target - consumed) / lengths[index] : 0
    return [
      points[index][0] + (points[index + 1][0] - points[index][0]) * ratio,
      points[index][1] + (points[index + 1][1] - points[index][1]) * ratio,
    ]
  }
  return [...points.at(-1)]
}

function samplePolyline(points, spacing, clearance, phase = 0) {
  const segments = points.slice(0, -1).map((start, index) => {
    const end = points[index + 1]
    const delta = [end[0] - start[0], end[1] - start[1]]
    return { start, direction: normalize(delta), length: Math.hypot(...delta) }
  }).filter((segment) => segment.length > 0.1)
  const total = segments.reduce((sum, segment) => sum + segment.length, 0)
  const samples = []
  for (let target = clearance + phase; target < total - clearance; target += spacing) {
    let consumed = 0
    for (const segment of segments) {
      if (consumed + segment.length >= target) {
        const amount = target - consumed
        samples.push({
          point: [
            segment.start[0] + segment.direction[0] * amount,
            segment.start[1] + segment.direction[1] * amount,
          ],
          direction: segment.direction,
        })
        break
      }
      consumed += segment.length
    }
  }
  return samples
}

function edgeWidth(edge) {
  return edge.roadWidth ?? edge.lanes.reduce((sum, lane) => sum + lane.width, 0)
}

function oppositeEdge(left, right, scale) {
  const leftPoints = left.centerline
  const rightPoints = right.centerline
  const leftDirection = normalize([
    leftPoints.at(-1)[0] - leftPoints[0][0],
    leftPoints.at(-1)[1] - leftPoints[0][1],
  ])
  const rightDirection = normalize([
    rightPoints.at(-1)[0] - rightPoints[0][0],
    rightPoints.at(-1)[1] - rightPoints[0][1],
  ])
  return dot(leftDirection, rightDirection) < -0.82
    && distance(leftPoints[0], rightPoints.at(-1)) < 42 * scale
    && distance(leftPoints.at(-1), rightPoints[0]) < 42 * scale
}

function physicalCorridors(manifest) {
  const scale = manifest.horizontalScale || 1
  const used = new Set()
  const corridors = []
  for (const edge of manifest.edges) {
    if (used.has(edge.id)) continue
    used.add(edge.id)
    const companion = manifest.edges.find((candidate) => (
      !used.has(candidate.id) && oppositeEdge(edge, candidate, scale)
    ))
    if (companion) used.add(companion.id)
    const primary = edge.centerline
    if (!companion) {
      corridors.push({ id: edge.id, points: primary, halfWidth: edgeWidth(edge) / 2 })
      continue
    }
    const secondary = companion.centerline
    const count = Math.max(20, Math.min(72, Math.max(primary.length, secondary.length)))
    const middle = Math.floor(count / 2)
    corridors.push({
      id: `${edge.id}|${companion.id}`,
      points: Array.from({ length: count }, (_, index) => {
        const progress = index / (count - 1)
        const left = pointAtProgress(primary, progress)
        const right = pointAtProgress(secondary, 1 - progress)
        return [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2]
      }),
      halfWidth: distance(
        pointAtProgress(primary, middle / (count - 1)),
        pointAtProgress(secondary, 1 - middle / (count - 1)),
      ) / 2 + Math.max(edgeWidth(edge), edgeWidth(companion)) / 2,
    })
  }
  return new Map(corridors.map((corridor) => [corridor.id, corridor]))
}

function toLocal(position, manifest) {
  const scale = manifest.horizontalScale || 1
  return [
    (position[0] - manifest.origin.longitude)
      * Math.cos(manifest.origin.latitude * Math.PI / 180)
      * METERS_PER_DEGREE
      * scale,
    (position[1] - manifest.origin.latitude) * METERS_PER_DEGREE * scale,
  ]
}

test('all 20 intersection streetlights face the road and remain outside the carriageway', async () => {
  let checked = 0
  for (let index = 1; index <= 20; index += 1) {
    const directory = new URL(`../public/intersections/v3/demo_${index}/`, import.meta.url)
    const [manifest, facilities, environment] = await Promise.all([
      readFile(new URL('manifest.json', directory), 'utf8').then(JSON.parse),
      readFile(new URL('facilities.json', directory), 'utf8').then(JSON.parse),
      readFile(new URL('environment.json', directory), 'utf8').then(JSON.parse),
    ])
    assert.equal(environment.streetlight.modelYawDegrees, 180)
    const corridors = physicalCorridors(manifest)
    for (const lamp of facilities.lamps) {
      const idParts = lamp.id.split(':')
      const corridorId = idParts.slice(1, -2).join(':')
      const side = Number(idParts.at(-2))
      const sampleIndex = Number(idParts.at(-1))
      const corridor = corridors.get(corridorId)
      assert.ok(corridor, `${manifest.intersectionId} ${lamp.id} corridor is missing`)
      const spacing = LAMP_SPACING_METERS * manifest.horizontalScale
      const samples = samplePolyline(
        corridor.points,
        spacing,
        LAMP_END_CLEARANCE_METERS * manifest.horizontalScale,
        side < 0 ? spacing / 2 : 0,
      )
      const sample = samples[sampleIndex]
      assert.ok(sample, `${manifest.intersectionId} ${lamp.id} sample is missing`)
      const position = toLocal(lamp.position, manifest)
      const normal = [-sample.direction[1], sample.direction[0]]
      const inward = [-normal[0] * side, -normal[1] * side]
      const heading = [Math.cos(lamp.heading), Math.sin(lamp.heading)]
      const angle = Math.acos(Math.max(-1, Math.min(1, dot(inward, heading))))
      assert.ok(
        angle <= MAX_HEADING_ERROR_RADIANS,
        `${manifest.intersectionId} ${lamp.id} points ${(angle * 180 / Math.PI).toFixed(2)} degrees away from the road`,
      )
      const offset = corridor.halfWidth + LAMP_ROADSIDE_CLEARANCE_METERS * manifest.horizontalScale
      const expectedPosition = [
        sample.point[0] + normal[0] * offset * side,
        sample.point[1] + normal[1] * offset * side,
      ]
      assert.ok(
        distance(position, expectedPosition) <= 0.02,
        `${manifest.intersectionId} ${lamp.id} does not match the audited roadside position`,
      )
      checked += 1
    }
  }
  assert.ok(checked > 5_000, `expected to audit all streetlights, checked ${checked}`)
})
