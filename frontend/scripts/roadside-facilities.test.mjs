import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { Color } from 'three'

import {
  RoadsideFacilityRenderer,
  resolveStreetlightHeading,
} from '../src/mapv/showcaseLayers/RoadsideFacilityRenderer.ts'
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
      { laneIndex: 1, movements: ['through', 'right'] },
      { laneIndex: 2, movements: ['through'] },
    ],
    south: [
      { laneIndex: 0, movements: ['through'] },
      { laneIndex: 1, movements: ['through'] },
      { laneIndex: 2, movements: ['left', 'through'] },
    ],
    west: [
      { laneIndex: 0, movements: ['right'] },
      { laneIndex: 1, movements: ['right'] },
      { laneIndex: 2, movements: ['left'] },
      { laneIndex: 3, movements: ['left'] },
      { laneIndex: 4, movements: ['left'] },
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
  const greenLenses = group.getObjectByName('traffic-signal-green')
  const signalIndex = manifest.signals.findIndex((signal) => signal.approach === 'north')
  assert.ok(greenLenses)
  const combinedArrow = group.getObjectByName('road-arrow-left-through')
  assert.ok(combinedArrow)
  assert.ok(group.getObjectByName('road-arrow-through-right'))
  for (const key of ['through', 'left', 'right', 'left-through', 'through-right']) {
    const arrow = group.getObjectByName(`road-arrow-${key}`)
    assert.ok(arrow, `missing complete ${key} marking`)
    arrow.geometry.computeBoundingBox()
    const bounds = arrow.geometry.boundingBox
    assert.ok(bounds.max.y - bounds.min.y >= 4.5 && bounds.max.y - bounds.min.y <= 5.5)
    assert.ok(bounds.max.x - bounds.min.x <= 3.35)
    assert.equal(arrow.geometry.userData.contourCount, 1)
  }

  for (const name of [
    'traffic-signal-arms',
    'traffic-signal-backs',
    'roadside-camera-brackets',
    'roadside-camera-lenses',
    'roadside-control-cabinets',
  ]) {
    assert.ok(group.getObjectByName(name), `missing ${name}`)
  }
  for (const name of ['street-lamp-arms', 'street-lamp-housings', 'street-lamp-lenses']) {
    assert.equal(group.getObjectByName(name), undefined, `legacy procedural lamp ${name} must be removed`)
  }
  const poles = group.getObjectByName('street-poles')
  assert.equal(poles.material.type, 'MeshStandardMaterial')

  renderer.setRealisticDetailActive(true)
  assert.equal(group.getObjectByName('roadside-furniture').visible, true)
  assert.equal(group.getObjectByName('legacy-traffic-signals').visible, false)
  assert.equal(group.getObjectByName('legacy-road-markings').visible, false)
  renderer.setRealisticDetailActive(false)
  assert.equal(group.getObjectByName('legacy-traffic-signals').visible, true)

  renderer.updateSignals([{ intersection_id: 'demo_2', current_phase: 1, stage: 'GREEN' }])
  const active = new Color()
  greenLenses.getColorAt(signalIndex, active)
  assert.ok(active.g > active.r)

  renderer.destroy()
  assert.deepEqual(removed, [group])
})

test('places street lamps on both sides near each controlled approach', () => {
  const manifest = buildSceneFacilityManifest(roads, tls, 'demo_2')
  const approachLamps = manifest.lamps.filter((lamp) => lamp.id.startsWith('lamp:approach:'))
  assert.equal(approachLamps.length, 12)
  for (const edgeId of ['-51425', '-56734', '-57228']) {
    const lamps = approachLamps.filter((lamp) => lamp.id.includes(`:${edgeId}:`))
    assert.equal(lamps.length, 4)
    assert.ok(lamps.some((lamp) => lamp.id.endsWith(':-1')))
    assert.ok(lamps.some((lamp) => lamp.id.endsWith(':1')))
  }
})

test('preserves opposite roadside headings and applies one model yaw calibration', () => {
  const yaw = 12 * Math.PI / 180
  const first = resolveStreetlightHeading({ heading: 0 }, yaw)
  const opposite = resolveStreetlightHeading({ heading: Math.PI }, yaw)
  const delta = Math.abs(((opposite - first + Math.PI * 3) % (Math.PI * 2)) - Math.PI)

  assert.ok(Math.abs(first - yaw) < 1e-12)
  assert.ok(Math.abs(delta - Math.PI) < 1e-12)
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
