import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  detectedMarkerColor,
  mergeSceneEventMarkers,
} from '../src/mapv/sceneEventMarkerRules.ts'

const position = (x = 0) => ({
  scene: [x, 0, 0],
  mapCoordinate: [116, 39, 0],
  source: 'detected_coordinates',
})

const detected = (id, eventType = null, laneId = 'edge_0') => ({
  event_id: id,
  status: 'active',
  traffic_state: 'spillback',
  display_type: 'spillback',
  display_label: '排队溢出',
  severity: 'medium',
  confidence: 0.9,
  intersection_id: 'demo_1',
  lane_ids: [laneId],
  longitude: 116,
  latitude: 39,
  start_seconds: 10,
  end_seconds: 40,
  duration_seconds: 30,
  evidence: [],
  suggestion: '',
  prediction_summary: '',
  event_type: eventType,
})

test('ordinary detections are yellow and detected accidents are red', () => {
  assert.equal(detectedMarkerColor(detected('ordinary')), 'yellow')
  assert.equal(detectedMarkerColor(detected('accident', 'accident')), 'red')
})

test('overlapping detected and runtime accidents merge without losing detail', () => {
  const card = detected('detected-1', 'accident')
  const runtime = {
    sessionId: 'session', eventId: 'runtime-1', intersectionId: 'demo_1',
    eventType: 'accident', startSeconds: 20, endSeconds: 50,
    parameters: {}, state: 'ACTIVE', error: null, details: { lane_id: 'edge_0' },
  }
  const merged = mergeSceneEventMarkers([
    {
      id: 'detected:detected-1', color: 'red', intersectionId: 'demo_1', position: position(),
      details: [{ kind: 'detected', id: 'detected:detected-1', card }],
    },
    {
      id: 'runtime:runtime-1', color: 'red', intersectionId: 'demo_1', position: position(2),
      details: [{ kind: 'runtime', id: 'runtime:runtime-1', event: runtime }],
    },
  ])
  assert.equal(merged.length, 1)
  assert.equal(merged[0].details.length, 2)
  assert.equal(merged[0].position.scene[0], 2)
})

test('3D scene keeps every non-cancelled runtime red marker and preserves legacy construction markers', async () => {
  const [source, markerSource, intersectionSource] = await Promise.all([
    readFile(new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/mapv/sceneEventMarkers.ts', import.meta.url), 'utf8'),
    readFile(new URL('../src/mapv/realistic/MapvRealisticIntersectionLayer.ts', import.meta.url), 'utf8'),
  ])
  assert.doesNotMatch(source, /event\.state !== 'ACTIVE'/)
  assert.match(source, /runtimeDisturbanceHasSceneMarker\(event\)/)
  assert.match(source, /resolveLaneScenePositionAny/)
  assert.match(source, /SceneEventMarkerOverlay/)
  assert.match(source, /projectMarkerToContainer\(\s*marker\.id/)
  assert.doesNotMatch(source, /<DetectedEventOverlay/)
  assert.match(markerSource, /EVENT_MARKER_SIZE_PIXELS = 42/)
  assert.match(markerSource, /EVENT_MARKER_HIT_SIZE_PIXELS = 48/)
  assert.doesNotMatch(markerSource, /new mapvthree\.DOMOverlay/)
  assert.doesNotMatch(markerSource, /scene-event-map-anchor/)
  assert.match(markerSource, /map\.projectArrayCoordinate\(marker\.position\.mapCoordinate\)/)
  assert.match(markerSource, /\.project\(camera\)/)
  assert.match(markerSource, /cameraVersion/)
  assert.match(intersectionSource, /map\.unprojectArrayCoordinate\(scene\)/)
})
