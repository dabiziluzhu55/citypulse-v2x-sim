import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { Color } from 'three'

import { RoadsideFacilityRenderer } from '../src/mapv/showcaseLayers/RoadsideFacilityRenderer.ts'
import { snapshotToTrafficView } from '../src/utils/trafficStateMerge.ts'
import {
  buildSceneFacilityManifest,
  parseSceneFacilityManifest,
  resolveSignalColor,
} from '../src/mapv/showcaseLayers/sceneFacilities.ts'

const roads = JSON.parse(await readFile(
  new URL('../../data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson', import.meta.url),
  'utf8',
))
const tls = JSON.parse(await readFile(
  new URL('../../data/maps/sumo/generated/manifests/tls_manifest.json', import.meta.url),
  'utf8',
))

test('derives stable roadside facilities from SUMO roads and TLS connections', () => {
  const first = buildSceneFacilityManifest(roads, tls, 'demo_2')
  const second = buildSceneFacilityManifest(roads, tls, 'demo_2')

  assert.deepEqual(second, first)
  assert.ok(first.lamps.length > 0)
  assert.ok(first.signals.length > 0)
  assert.ok(first.cameras.length > 0)
  assert.ok(first.arrows.some((arrow) => arrow.movements.includes('left')))
  assert.ok(first.arrows.some((arrow) => arrow.movements.includes('through')))
})

test('derives one combined road marking per inbound SUMO lane', () => {
  const manifest = buildSceneFacilityManifest(roads, tls, 'demo_2')
  const markings = Object.fromEntries(
    ['north', 'south', 'west'].map((approach) => [
      approach,
      manifest.arrows
        .filter((arrow) => arrow.approach === approach)
        .map((arrow) => ({ laneIndex: arrow.laneIndex, movements: arrow.movements })),
    ]),
  )

  assert.deepEqual(markings, {
    north: [
      { laneIndex: 0, movements: ['through', 'right'] },
      { laneIndex: 1, movements: ['through'] },
    ],
    south: [
      { laneIndex: 0, movements: ['through'] },
      { laneIndex: 1, movements: ['left', 'through'] },
    ],
    west: [
      { laneIndex: 0, movements: ['right'] },
      { laneIndex: 1, movements: ['left'] },
    ],
  })
})

test('maps backend phase stages to safe traffic-light colors', () => {
  const manifest = buildSceneFacilityManifest(roads, tls, 'demo_2')
  const north = manifest.signals.find((signal) => signal.approach === 'north')
  assert.ok(north)

  assert.equal(resolveSignalColor(manifest, north, 1, 'GREEN'), 'green')
  assert.equal(resolveSignalColor(manifest, north, 1, 'YELLOW'), 'yellow')
  assert.equal(resolveSignalColor(manifest, north, 1, 'CLEARANCE'), 'red')
  assert.equal(resolveSignalColor(manifest, north, null, null), 'red')
})

test('rejects TLS connections that do not exist in the road dataset', () => {
  const invalidTls = structuredClone(tls)
  invalidTls.intersections.demo_2.connections[0].from_edge = 'missing-edge'

  assert.throws(
    () => buildSceneFacilityManifest(roads, invalidTls, 'demo_2'),
    /missing-edge/,
  )
})

test('rejects an incompatible generated facility asset', () => {
  assert.throws(
    () => parseSceneFacilityManifest({ schemaVersion: 1 }),
    /schemaVersion 2/,
  )
})

test('renders one facility group and updates signal colors without rebuilding it', () => {
  const manifest = buildSceneFacilityManifest(roads, tls, 'demo_2')
  const added = []
  const removed = []
  const engine = {
    map: { projectArrayCoordinate: (coordinate) => coordinate },
    add: (object) => {
      added.push(object)
      return object
    },
    remove: (object) => removed.push(object),
    requestRender: () => {},
  }
  const renderer = new RoadsideFacilityRenderer(engine, (coordinate) => [...coordinate])

  renderer.render(manifest)
  assert.equal(added.length, 1)
  const group = added[0]
  const greenLenses = group.children.find((child) => child.name === 'traffic-signal-green')
  const signalIndex = manifest.signals.findIndex((signal) => signal.approach === 'north')
  assert.ok(greenLenses)
  const combinedArrow = group.children.find((child) => child.name === 'road-arrow-left-through')
  assert.ok(combinedArrow)
  assert.ok(group.children.some((child) => child.name === 'road-arrow-through-right'))
  for (const key of ['through', 'left', 'right', 'left-through', 'through-right']) {
    const arrow = group.children.find((child) => child.name === `road-arrow-${key}`)
    assert.ok(arrow, `missing complete ${key} marking`)
    arrow.geometry.computeBoundingBox()
    const bounds = arrow.geometry.boundingBox
    assert.ok(bounds.max.y - bounds.min.y >= 4.5 && bounds.max.y - bounds.min.y <= 5.5)
    assert.ok(bounds.max.x - bounds.min.x <= 3.35)
    assert.equal(arrow.geometry.userData.contourCount, 1)
  }

  for (const name of [
    'street-lamp-arms',
    'street-lamp-housings',
    'street-lamp-lenses',
    'traffic-signal-arms',
    'traffic-signal-backs',
    'roadside-camera-brackets',
    'roadside-camera-lenses',
  ]) {
    assert.ok(group.children.some((child) => child.name === name), `missing ${name}`)
  }
  const poles = group.children.find((child) => child.name === 'street-poles')
  assert.equal(poles.material.type, 'MeshStandardMaterial')

  renderer.updateSignals([{ intersection_id: 'demo_2', current_phase: 1, stage: 'GREEN' }])
  const active = new Color()
  greenLenses.getColorAt(signalIndex, active)
  assert.ok(active.g > active.r)

  renderer.destroy()
  assert.deepEqual(removed, [group])
})

test('preserves the backend signal stage for traffic-light rendering', () => {
  const view = snapshotToTrafficView({
    session_id: 'session-1',
    state: 'RUNNING',
    sequence: 1,
    elapsed_seconds: 1,
    duration_seconds: 60,
    progress: 1 / 60,
    official_time: '07:00:01',
    intersections: {
      demo_2: {
        current_phase: 1,
        pending_phase: null,
        stage: 'YELLOW',
        stage_elapsed: 1,
        lanes: {},
      },
    },
    vehicles: [],
    events: [],
    metrics: {},
    error: null,
  })

  assert.equal(view.intersections[0].stage, 'YELLOW')
})
