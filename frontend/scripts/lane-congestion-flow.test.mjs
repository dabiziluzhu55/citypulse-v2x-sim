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

test('lane flow renders only the congested driving lane and preserves point order', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/intersections/v3/demo_1/manifest.json', import.meta.url),
    'utf8',
  ))
  const edge = manifest.edges.find((candidate) => (
    candidate.lanes.some((lane) => lane.kind === 'driving')
    && candidate.lanes.some((lane) => lane.kind !== 'driving')
  )) ?? manifest.edges.find((candidate) => candidate.lanes.some((lane) => lane.kind === 'driving'))
  assert.ok(edge)
  const drivingLanes = edge.lanes.filter((lane) => (lane.kind ?? 'driving') === 'driving')
  const congestedLane = drivingLanes[0]
  const result = buildLaneCongestionFlows(manifest, {
    sessionId: 'session-1', presentationGeneration: 1, sequence: 1, asOfSeconds: 1,
    edgeIdsWithLaneMetrics: new Set([edge.id]), diagnostics: {},
    lanes: Object.fromEntries(drivingLanes.map((lane, index) => [lane.id, {
      laneId: lane.id, edgeId: edge.id, intersectionId: manifest.intersectionId,
      ownerIntersectionId: manifest.intersectionId, vehicleCount: index ? 0 : 20,
      haltingCount: index ? 0 : 16, meanSpeed: index ? 12 : 0.5,
      occupancyPct: index ? 0 : 90, level: index ? 'free' : 'severe',
      instantaneousLevel: index ? 'free' : 'severe', fallbackReason: null,
    }])),
  })
  assert.equal(result.flows.length, 1)
  assert.ok(result.flows.every((flow) => flow.level === 'severe' && flow.direction === 'forward'))
  for (const flow of result.flows) {
    assert.equal(flow.laneId, congestedLane.id)
    const lane = drivingLanes.find((candidate) => candidate.id === flow.laneId)
    const source = lane.vehicleGuidePoints ?? lane.renderPoints ?? lane.points
    assert.equal(flow.mapCoordinates.length, source.length)
    assert.notDeepEqual(flow.mapCoordinates[0], flow.mapCoordinates.at(-1))
  }

  const free = buildLaneCongestionFlows(manifest, null)
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
