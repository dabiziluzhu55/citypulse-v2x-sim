import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildLaneCongestionFlows,
  CONGESTION_FLOW_VISUALS,
  congestionAnimationSpeed,
  LaneFlowSpeedBucketStabilizer,
} from '../src/mapv/realistic/laneCongestionFlow.ts'

const laneLayerSource = await readFile(
  new URL('../src/mapv/realistic/LaneCongestionFlowLayer.ts', import.meta.url),
  'utf8',
)
const topologyLayerSource = await readFile(
  new URL('../src/mapv/IntersectionTopologyLayer.ts', import.meta.url),
  'utf8',
)

test('backend congestion levels map to the required 3D flow palette', () => {
  assert.equal(CONGESTION_FLOW_VISUALS.free.visible, false)
  assert.equal(CONGESTION_FLOW_VISUALS.slow.color, '#ffe978')
  assert.equal(CONGESTION_FLOW_VISUALS.congested.color, '#ffd21f')
  assert.equal(CONGESTION_FLOW_VISUALS.severe.color, '#ff3141')
  assert.ok(CONGESTION_FLOW_VISUALS.congested.lineWidth > CONGESTION_FLOW_VISUALS.slow.lineWidth)
  assert.ok(congestionAnimationSpeed(0) < congestionAnimationSpeed(12))
})

test('lane flow projects an edge level only to driving lanes and preserves point order', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_1/manifest.json', import.meta.url),
    'utf8',
  ))
  const edge = manifest.edges.find((candidate) => (
    candidate.lanes.some((lane) => lane.kind === 'driving')
    && candidate.lanes.some((lane) => lane.kind !== 'driving')
  )) ?? manifest.edges.find((candidate) => candidate.lanes.some((lane) => lane.kind === 'driving'))
  assert.ok(edge)
  const result = buildLaneCongestionFlows(manifest, {
    as_of_seconds: 1,
    edges: {
      [edge.id]: {
        level: 'severe', score: 1, mean_speed: 2, occupancy_pct: 90,
        vehicle_count: 20, halting_count: 16,
      },
    },
  })
  const drivingLanes = edge.lanes.filter((lane) => (lane.kind ?? 'driving') === 'driving')
  assert.equal(result.flows.length, drivingLanes.length)
  assert.ok(result.flows.every((flow) => flow.level === 'severe' && flow.direction === 'forward'))
  for (const flow of result.flows) {
    const lane = drivingLanes.find((candidate) => candidate.id === flow.laneId)
    const source = lane.vehicleGuidePoints ?? lane.renderPoints ?? lane.points
    assert.equal(flow.mapCoordinates.length, source.length)
    assert.notDeepEqual(flow.mapCoordinates[0], flow.mapCoordinates.at(-1))
  }

  const free = buildLaneCongestionFlows(manifest, {
    as_of_seconds: 2,
    edges: { [edge.id]: { level: 'free', mean_speed: 10 } },
  })
  assert.equal(free.flows.length, 0)
})

test('global 3D flow omits free routes and clips around local LOD intersections', async () => {
  const source = await readFile(
    new URL('../src/mapv/IntersectionTopologyLayer.ts', import.meta.url),
    'utf8',
  )
  assert.match(source, /if \(level === 'free'\) continue/)
  assert.match(source, /setLocalFlowIntersections/)
  assert.match(source, /clippedFlowFeatures/)
})

test('confirms a lane speed bucket for three snapshots before rebuilding its source', () => {
  const stabilizer = new LaneFlowSpeedBucketStabilizer()
  assert.deepEqual(stabilizer.resolve('edge:lane', 'low'), { bucket: 'low', suppressed: false })
  assert.deepEqual(stabilizer.resolve('edge:lane', 'high'), { bucket: 'low', suppressed: true })
  assert.deepEqual(stabilizer.resolve('edge:lane', 'high'), { bucket: 'low', suppressed: true })
  assert.deepEqual(stabilizer.resolve('edge:lane', 'high'), { bucket: 'high', suppressed: false })
})

test('uses the reduced-frequency local and global flow animation contract', () => {
  assert.match(laneLayerSource, /bucket === 'low' \? 0\.18 : bucket === 'medium' \? 0\.30 : 0\.44/)
  assert.match(laneLayerSource, /animationInterval: 4/)
  assert.match(laneLayerSource, /animationTailRatio: 0\.28/)
  assert.match(laneLayerSource, /animationIdle: 1_500/)
  assert.match(topologyLayerSource, /animationInterval: 4/)
  assert.match(topologyLayerSource, /animationTailRatio: 0\.24/)
  assert.match(topologyLayerSource, /animationSpeed: 0\.50/)
  assert.match(topologyLayerSource, /animationIdle: 2_400/)
})
