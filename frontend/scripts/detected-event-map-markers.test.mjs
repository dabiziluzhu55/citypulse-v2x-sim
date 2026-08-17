import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { groupDetectedEventMapMarkers } from '../src/utils/detectedEventMapMarkers.ts'

const backgroundMapSource = await readFile(
  new URL('../src/components/visualization/AppBackgroundMap.vue', import.meta.url),
  'utf8',
)

test('groups nearby detected events without moving their geographic anchor', () => {
  const cards = [
    { event_id: 'a', longitude: 116.1, latitude: 39.1 },
    { event_id: 'b', longitude: 116.10001, latitude: 39.1 },
    { event_id: 'c', longitude: 116.101, latitude: 39.1 },
  ]
  const markers = groupDetectedEventMapMarkers(cards, 8)
  assert.equal(markers.length, 2)
  assert.deepEqual(markers[0].cards.map((card) => card.event_id), ['a', 'b'])
  assert.equal(markers[0].longitude, cards[0].longitude)
  assert.equal(markers[0].latitude, cards[0].latitude)
})

test('renders detected events as native OpenLayers features', () => {
  assert.match(backgroundMapSource, /const detectedEventSource = new VectorSource\(\)/)
  assert.match(backgroundMapSource, /const detectedEventLayer = new VectorLayer/)
  assert.match(backgroundMapSource, /anchor:\s*\[0\.5, 1\]/)
  assert.match(backgroundMapSource, /layer === detectedEventLayer/)
  assert.doesNotMatch(backgroundMapSource, /<DetectedEventOverlay/)
  assert.doesNotMatch(backgroundMapSource, /projectDetectedEventToOverlay/)
  assert.doesNotMatch(backgroundMapSource, /overlayViewToken/)
})
