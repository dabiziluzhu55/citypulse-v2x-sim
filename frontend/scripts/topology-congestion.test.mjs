import assert from 'node:assert/strict'
import test from 'node:test'

import {
  classifyLaneCongestion,
  LaneCongestionStateResolver,
  normalizeLaneOccupancyPct,
} from '../src/utils/laneCongestionState.ts'

const metrics = (vehicleCount, haltingCount, meanSpeed, occupancyPct) => ({
  vehicleCount, haltingCount, meanSpeed, occupancyPct,
})

test('classifies lane congestion with the backend thresholds', () => {
  assert.equal(classifyLaneCongestion(metrics(2, 2, 0, 100)), 'free')
  assert.equal(classifyLaneCongestion(metrics(4, 2, 7, 12)), 'slow')
  assert.equal(classifyLaneCongestion(metrics(8, 4, 2.5, 22)), 'congested')
  assert.equal(classifyLaneCongestion(metrics(10, 6, 0.8, 36)), 'severe')
  assert.equal(normalizeLaneOccupancyPct(0.35), 35)
  assert.equal(normalizeLaneOccupancyPct(35), 35)
})

function intersections(level) {
  const runtime = level === 'severe'
    ? { vehicle_count: 10, halting_count: 7, mean_speed: 0.5, occupancy: 45, edge_id: 'E1' }
    : level === 'slow'
      ? { vehicle_count: 5, halting_count: 2, mean_speed: 5, occupancy: 12, edge_id: 'E1' }
      : { vehicle_count: 1, halting_count: 0, mean_speed: 12, occupancy: 1, edge_id: 'E1' }
  return { demo_1: { current_phase: 0, pending_phase: null, stage: 'GREEN', stage_elapsed: 0, lanes: { E1_0: runtime } } }
}

const laneEntries = [{
  laneId: 'E1_0', edgeId: 'E1', kind: 'driving', intersectionId: 'demo_1',
  coordinates: [[116, 39], [116.001, 39]],
}]

test('confirms a lane level change for two snapshots and resets by session generation', () => {
  const resolver = new LaneCongestionStateResolver()
  const resolve = (sequence, level, generation = 1) => resolver.resolve({
    sessionId: 'session-1', presentationGeneration: generation, sequence,
    asOfSeconds: sequence, intersections: intersections(level), laneEntries,
  }).lanes.E1_0.level
  assert.equal(resolve(1, 'free'), 'free')
  assert.equal(resolve(2, 'severe'), 'free')
  assert.equal(resolve(3, 'severe'), 'severe')
  assert.equal(resolve(4, 'free'), 'severe')
  assert.equal(resolve(5, 'free'), 'free')
  assert.equal(resolve(6, 'slow', 2), 'slow')
})

test('prefers the indexed owner when one lane appears in adjacent intersections', () => {
  const resolver = new LaneCongestionStateResolver()
  const snapshot = resolver.resolve({
    sessionId: 'session-2', presentationGeneration: 1, sequence: 1, asOfSeconds: 1,
    laneEntries,
    intersections: {
      ...intersections('free'),
      demo_2: {
        current_phase: 0, pending_phase: null, stage: 'GREEN', stage_elapsed: 0,
        lanes: { E1_0: { vehicle_count: 20, halting_count: 20, mean_speed: 0, occupancy: 90, edge_id: 'E1' } },
      },
    },
  })
  assert.equal(snapshot.lanes.E1_0.intersectionId, 'demo_1')
  assert.equal(snapshot.lanes.E1_0.level, 'free')
  assert.equal(snapshot.diagnostics.duplicateLaneCount, 1)
  assert.equal(snapshot.diagnostics.duplicateConflictCount, 1)
})
