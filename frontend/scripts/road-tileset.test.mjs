import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import * as roadTilesetGenerator from './generate-road-tileset.mjs'
import * as staticRoadTileset from '../src/mapv/staticRoadTileset.ts'
import { DEMO_2_SOURCE_CENTER_BD09 } from '../src/mapv/sceneCoordinates.ts'

const { buildRoadTileset } = roadTilesetGenerator
const { roadTilesetMatchesResponse } = staticRoadTileset

const source = JSON.parse(readFileSync(
  new URL('../../data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson', import.meta.url),
  'utf8',
))

const response = {
  intersection_id: source.metadata.intersection_id,
  center: source.metadata.center,
  radius_m: source.metadata.radius_m,
  bounds: {
    west: 116.1198435,
    south: 38.985877,
    east: 116.1331276,
    north: 38.9965438,
  },
  geojson: source,
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

test('SUMO roads produce a valid deterministic single-tile GLB', async () => {
  const first = await buildRoadTileset(response)
  const second = await buildRoadTileset(response)

  assert.equal(first.glb.subarray(0, 4).toString('utf8'), 'glTF')
  assert.equal(first.glb.readUInt32LE(4), 2)
  assert.equal(first.glb.readUInt32LE(8), first.glb.length)
  assert.equal(first.tileset.asset.version, '1.1')
  assert.ok(first.tileset.geometricError > 0)
  assert.equal(first.tileset.root.content.uri, 'tiles/road.glb')
  assert.equal(first.tileset.root.geometricError, 0)
  assert.equal(first.tileset.root.transform.length, 16)
  assert.ok(first.tileset.root.transform.every(Number.isFinite))
  assert.ok(first.tileset.root.transform[12] > 12_000_000)
  assert.ok(first.tileset.root.transform[13] > 4_000_000)
  assert.equal(first.manifest.intersection_id, 'demo_2')
  assert.equal(first.manifest.placement_mode, 'actual')
  assert.deepEqual(first.manifest.placement_bd09, DEMO_2_SOURCE_CENTER_BD09)
  assert.equal(first.manifest.feature_count, 15)
  assert.equal(first.manifest.source_generated_at, source.metadata.generated_at)
  assert.match(first.manifest.source_sha256, /^[a-f0-9]{64}$/)
  assert.equal(sha256(first.glb), sha256(second.glb))
  assert.deepEqual(first.tileset, second.tileset)
  assert.deepEqual(first.manifest, second.manifest)

  const box = first.tileset.root.boundingVolume.box
  assert.equal(box.length, 12)
  assert.ok(box.every(Number.isFinite))
  assert.ok(box[3] > 100 && box[3] < 1_000)
  assert.ok(box[7] > 100 && box[7] < 1_000)
  assert.ok(first.glb.length < 2 * 1024 * 1024)
})

test('static road tiles are used only for the matching backend dataset', async () => {
  const { manifest } = await buildRoadTileset(response)
  const apiResponse = {
    ...response,
    geojson: {
      ...response.geojson,
      features: [
        ...response.geojson.features,
        {
          type: 'Feature',
          properties: { feature_type: 'intersection' },
          geometry: { type: 'Point', coordinates: [116.126756, 38.99115] },
        },
      ],
    },
  }

  assert.equal(roadTilesetMatchesResponse(manifest, apiResponse, 'actual'), true)
  assert.equal(roadTilesetMatchesResponse(manifest, apiResponse, 'xiongan-demo'), false)
  assert.equal(roadTilesetMatchesResponse(manifest, {
    ...apiResponse,
    geojson: {
      ...apiResponse.geojson,
      metadata: { ...apiResponse.geojson.metadata, generated_at: 'new-version' },
    },
  }, 'actual'), false)
  assert.equal(roadTilesetMatchesResponse(manifest, {
    ...apiResponse,
    geojson: { ...apiResponse.geojson, features: apiResponse.geojson.features.slice(1) },
  }, 'actual'), false)
  assert.equal(roadTilesetMatchesResponse({
    ...manifest,
    origin_wgs84: [manifest.origin_wgs84[0] + 0.001, manifest.origin_wgs84[1]],
  }, apiResponse, 'actual'), false)
  assert.equal(roadTilesetMatchesResponse({
    ...manifest,
    placement_bd09: [manifest.placement_bd09[0] + 0.001, manifest.placement_bd09[1]],
  }, apiResponse, 'actual'), false)
})

test('static road manifest rejects a mismatched coordinate contract without backend data', async () => {
  assert.equal(typeof staticRoadTileset.roadTilesetManifestIsValid, 'function')
  const { manifest } = await buildRoadTileset(response)

  assert.equal(staticRoadTileset.roadTilesetManifestIsValid(manifest, 'actual'), true)
  assert.equal(staticRoadTileset.roadTilesetManifestIsValid({
    ...manifest,
    coordinate_system: 'LOCAL_ENU_METERS',
  }, 'actual'), false)
  assert.equal(staticRoadTileset.roadTilesetManifestIsValid({
    ...manifest,
    placement_bd09: [manifest.placement_bd09[0] + 0.001, manifest.placement_bd09[1]],
  }, 'actual'), false)
})

test('road vertices use BD-09 WebMercator coordinates relative to their source center', () => {
  assert.equal(typeof roadTilesetGenerator.createLocalBaiduPlaneProjector, 'function')
  const project = roadTilesetGenerator.createLocalBaiduPlaneProjector(response.center)

  const center = project([response.center.longitude, response.center.latitude, 0])
  const east = project([response.center.longitude + 0.001, response.center.latitude, 2])

  assert.ok(Math.hypot(center[0], center[1]) < 1e-6)
  assert.equal(center[2], 0)
  assert.ok(Math.abs(east[0] - 110.4419082608074) < 1e-6)
  assert.ok(Math.abs(east[1] - 2.4055097829550505) < 1e-6)
  assert.equal(east[2], 2)
})
