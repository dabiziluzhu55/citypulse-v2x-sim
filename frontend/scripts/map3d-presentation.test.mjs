import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  assertGlobalBuildingSource,
  buildingTilesetManifestUrl,
  diagnoseGlobalBuildingSource,
} from '../src/mapv/buildingTilesetSource.ts'
import {
  advanceStableTileSamples,
  BUILDING_STABLE_SAMPLE_COUNT,
  BUILDING_STABLE_SAMPLE_INTERVAL_MS,
  FINAL_RENDER_FRAME_COUNT,
  MAP3D_PRESENTATION_TIMEOUT_MS,
  map3dLoadingStage,
  map3dPresentationReady,
} from '../src/mapv/map3dPresentationReadiness.ts'

const globalManifest = JSON.parse(await readFile(
  new URL('../public/3dtiles/xiongan-webmercator/manifest.json', import.meta.url),
  'utf8',
))
const focusedManifest = JSON.parse(await readFile(
  new URL('../public/3dtiles/xiongan-webmercator-demo_2/manifest.json', import.meta.url),
  'utf8',
))

test('accepts the global building source and rejects a focused intersection source', () => {
  const global = assertGlobalBuildingSource(globalManifest)
  assert.equal(global.kind, 'global')
  assert.equal(global.outputTiles, 977)
  assert.equal(global.vertexCount, 5_072_230)

  assert.equal(diagnoseGlobalBuildingSource(focusedManifest).kind, 'focused')
  assert.throws(
    () => assertGlobalBuildingSource(focusedManifest),
    /不能使用单路口裁剪建筑源/,
  )
})

test('resolves the building manifest beside the configured tileset', () => {
  assert.equal(
    buildingTilesetManifestUrl(
      '/3dtiles/xiongan-webmercator/tileset.json',
      'http://127.0.0.1:5173/dashboard',
    ).href,
    'http://127.0.0.1:5173/3dtiles/xiongan-webmercator/manifest.json',
  )
})

test('requires three consecutive idle building samples and resets on activity', () => {
  const idle = { hasContent: true, pendingRequests: 0, processingTiles: 0 }
  const active = { hasContent: true, pendingRequests: 1, processingTiles: 0 }
  let samples = advanceStableTileSamples(0, idle)
  samples = advanceStableTileSamples(samples, idle)
  assert.equal(samples, BUILDING_STABLE_SAMPLE_COUNT - 1)
  assert.equal(advanceStableTileSamples(samples, active), 0)
  samples = advanceStableTileSamples(0, idle)
  samples = advanceStableTileSamples(samples, idle)
  samples = advanceStableTileSamples(samples, idle)
  assert.equal(samples, BUILDING_STABLE_SAMPLE_COUNT)
})

test('opens the 3D presentation only after every required stage is ready', () => {
  const signals = {
    providerReady: true,
    cameraReady: true,
    overviewReady: true,
    intersectionReady: true,
    environmentReady: true,
    buildingRequired: true,
    buildingStableSamples: BUILDING_STABLE_SAMPLE_COUNT,
  }
  assert.equal(map3dPresentationReady(signals), true)
  assert.equal(map3dPresentationReady({ ...signals, cameraReady: false }), false)
  assert.equal(map3dPresentationReady({ ...signals, environmentReady: false }), false)
  assert.equal(map3dPresentationReady({ ...signals, buildingStableSamples: 2 }), false)
  assert.match(map3dLoadingStage({ ...signals, environmentReady: false }), /路灯与路口设施/)
})

test('keeps the agreed loading timing constants stable', () => {
  assert.equal(MAP3D_PRESENTATION_TIMEOUT_MS, 25_000)
  assert.equal(BUILDING_STABLE_SAMPLE_INTERVAL_MS, 250)
  assert.equal(FINAL_RENDER_FRAME_COUNT, 2)
})
