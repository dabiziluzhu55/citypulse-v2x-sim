import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  assertGlobalBuildingSource,
  buildingTilesetManifestUrl,
  diagnoseGlobalBuildingSource,
} from '../src/mapv/buildingTilesetSource.ts'
import {
  advanceBuildingLoadTracker,
  BUILDING_STABLE_SAMPLE_COUNT,
  BUILDING_STABLE_SAMPLE_INTERVAL_MS,
  buildingLoadStalled,
  buildingPresentationSettled,
  buildingPresentationUsable,
  buildingSoftPresentationUsable,
  createBuildingLoadTracker,
  FINAL_RENDER_FRAME_COUNT,
  MAP3D_MODULE_LOAD_TIMEOUT_MS,
  MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
  MAP3D_PRESENTATION_SOFT_TIMEOUT_MS,
  MAP3D_STALL_WINDOW_MS,
  map3dLoadingStage,
  map3dPresentationReady,
  resolveMap3dPresentationDecision,
} from '../src/mapv/map3dPresentationReadiness.ts'

const globalManifest = JSON.parse(await readFile(
  new URL('../public/3dtiles/xiongan-webmercator/manifest.json', import.meta.url),
  'utf8',
))
const focusedManifest = JSON.parse(await readFile(
  new URL('../public/3dtiles/xiongan-webmercator-demo_2/manifest.json', import.meta.url),
  'utf8',
))
const baiduThreeMapSource = await readFile(
  new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
  'utf8',
)
const appThreeMapLoaderSource = await readFile(
  new URL('../src/components/visualization/AppThreeMapLoader.vue', import.meta.url),
  'utf8',
)
const appSource = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')

function sample(overrides = {}) {
  return {
    readyTiles: 600,
    pendingRequests: 18,
    processingTiles: 0,
    attemptedRequests: 0,
    totalTiles: 977,
    cameraRevision: 1,
    nowMs: 1_000,
    ...overrides,
  }
}

function advanceSamples(tracker, nextSample, quality, count) {
  let next = tracker
  for (let index = 0; index < count; index += 1) {
    next = advanceBuildingLoadTracker(next, {
      ...nextSample,
      nowMs: nextSample.nowMs + index * BUILDING_STABLE_SAMPLE_INTERVAL_MS,
    }, quality)
  }
  return next
}

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

test('opens with usable coverage even while a small request tail remains', () => {
  const tracker = advanceSamples(
    createBuildingLoadTracker(1_000, 1),
    sample(),
    'full',
    BUILDING_STABLE_SAMPLE_COUNT + 1,
  )

  assert.equal(tracker.activeRequests, 18)
  assert.ok(tracker.coverage >= 0.97)
  assert.equal(buildingPresentationUsable(tracker), true)
  assert.equal(buildingPresentationSettled(tracker), false)
})

test('resets consecutive usable samples when visible demand grows', () => {
  let tracker = advanceSamples(
    createBuildingLoadTracker(1_000, 1),
    sample(),
    'full',
    BUILDING_STABLE_SAMPLE_COUNT,
  )
  assert.equal(tracker.usableSamples, BUILDING_STABLE_SAMPLE_COUNT - 1)

  tracker = advanceBuildingLoadTracker(tracker, sample({
    readyTiles: 620,
    nowMs: 2_000,
  }), 'full')
  assert.equal(tracker.usableSamples, 0)
  assert.equal(tracker.demandedTiles, 638)
})

test('uses a lower usable threshold for constrained WebGL devices', () => {
  const reducedSample = sample({ readyTiles: 300, pendingRequests: 18 })
  const full = advanceSamples(
    createBuildingLoadTracker(1_000, 1),
    reducedSample,
    'full',
    BUILDING_STABLE_SAMPLE_COUNT + 1,
  )
  const reduced = advanceSamples(
    createBuildingLoadTracker(1_000, 1),
    reducedSample,
    'reduced',
    BUILDING_STABLE_SAMPLE_COUNT + 1,
  )

  assert.equal(buildingPresentationUsable(full), false)
  assert.equal(buildingPresentationUsable(reduced), true)
})

test('requires real building content and tracks complete settlement separately', () => {
  const empty = advanceSamples(
    createBuildingLoadTracker(0, 1),
    sample({ readyTiles: 0, pendingRequests: 0, totalTiles: 977 }),
    'full',
    4,
  )
  assert.equal(buildingPresentationUsable(empty), false)
  assert.equal(buildingPresentationSettled(empty), false)

  const settled = advanceSamples(
    createBuildingLoadTracker(0, 1),
    sample({ readyTiles: 977, pendingRequests: 0 }),
    'full',
    BUILDING_STABLE_SAMPLE_COUNT + 1,
  )
  assert.equal(buildingPresentationUsable(settled), true)
  assert.equal(buildingPresentationSettled(settled), true)
})

test('allows a progressing soft presentation and rejects a stalled load', () => {
  let tracker = createBuildingLoadTracker(0, 1)
  tracker = advanceBuildingLoadTracker(tracker, sample({
    readyTiles: 200,
    pendingRequests: 30,
    nowMs: MAP3D_PRESENTATION_SOFT_TIMEOUT_MS,
  }), 'full')

  assert.equal(
    buildingSoftPresentationUsable(tracker, MAP3D_PRESENTATION_SOFT_TIMEOUT_MS + 1_000),
    true,
  )
  assert.equal(
    buildingLoadStalled(tracker, MAP3D_PRESENTATION_SOFT_TIMEOUT_MS + MAP3D_STALL_WINDOW_MS),
    true,
  )
  assert.equal(
    buildingSoftPresentationUsable(
      tracker,
      MAP3D_PRESENTATION_SOFT_TIMEOUT_MS + MAP3D_STALL_WINDOW_MS,
    ),
    false,
  )
})

test('makes soft reveal and hard timeout decisions without requiring request settlement', () => {
  const signals = {
    providerReady: true,
    cameraReady: true,
    overviewReady: true,
    intersectionReady: true,
    environmentReady: true,
    buildingRequired: true,
    buildingUsable: false,
    buildingReadyTiles: 200,
    buildingCoverage: 0.87,
  }
  const progressing = advanceBuildingLoadTracker(
    createBuildingLoadTracker(MAP3D_PRESENTATION_SOFT_TIMEOUT_MS, 1),
    sample({
      readyTiles: 200,
      pendingRequests: 30,
      nowMs: MAP3D_PRESENTATION_SOFT_TIMEOUT_MS,
    }),
    'full',
  )
  assert.equal(
    resolveMap3dPresentationDecision(
      signals,
      progressing,
      MAP3D_PRESENTATION_SOFT_TIMEOUT_MS,
      MAP3D_PRESENTATION_SOFT_TIMEOUT_MS + 1,
    ),
    'present',
  )

  const recentlyProgressed = {
    ...createBuildingLoadTracker(55_000, 1),
    lastProgressAtMs: 55_000,
  }
  assert.equal(
    resolveMap3dPresentationDecision(
      { ...signals, buildingReadyTiles: 0, buildingCoverage: 0 },
      recentlyProgressed,
      MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
      MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
    ),
    'hard-timeout',
  )
})

test('opens the 3D presentation only after every core stage is ready', () => {
  const signals = {
    providerReady: true,
    cameraReady: true,
    overviewReady: true,
    intersectionReady: true,
    environmentReady: true,
    buildingRequired: true,
    buildingUsable: true,
    buildingReadyTiles: 600,
    buildingCoverage: 0.98,
  }
  assert.equal(map3dPresentationReady(signals), true)
  assert.equal(map3dPresentationReady({ ...signals, cameraReady: false }), false)
  assert.equal(map3dPresentationReady({ ...signals, environmentReady: false }), false)
  assert.equal(map3dPresentationReady({ ...signals, buildingUsable: false }), false)
  assert.match(
    map3dLoadingStage({ ...signals, buildingUsable: false }),
    /已准备 600 · 覆盖 98%/,
  )
})

test('keeps module, soft, hard, and stall timing separate', () => {
  assert.equal(MAP3D_MODULE_LOAD_TIMEOUT_MS, 15_000)
  assert.equal(MAP3D_PRESENTATION_SOFT_TIMEOUT_MS, 30_000)
  assert.equal(MAP3D_PRESENTATION_HARD_TIMEOUT_MS, 60_000)
  assert.equal(MAP3D_STALL_WINDOW_MS, 10_000)
  assert.equal(BUILDING_STABLE_SAMPLE_INTERVAL_MS, 250)
  assert.equal(FINAL_RENDER_FRAME_COUNT, 2)
})

test('initializes the overview before buildings and does not focus the initial intersection', () => {
  const overviewCamera = baiduThreeMapSource.indexOf("mapView.setCameraPreset('overview')")
  const initialScene = baiduThreeMapSource.indexOf('const initialSceneReady = await switchRealisticIntersection')
  const buildingCreation = baiduThreeMapSource.indexOf('addGlobalBuildingTileset()', initialScene)

  assert.ok(overviewCamera >= 0)
  assert.ok(initialScene > overviewCamera)
  assert.ok(buildingCreation > initialScene)
  assert.match(
    baiduThreeMapSource.slice(initialScene, buildingCreation),
    /activeIntersectionId\.value,\s*true,\s*false,/,
  )
  assert.match(baiduThreeMapSource, /void switchRealisticIntersection\(intersectionId\)/)
  assert.match(baiduThreeMapSource, /cullRequestsWhileMoving:\s*true/)
})

test('separates module timeout, scene timeout, retry teardown, and overlay interaction', () => {
  assert.match(
    appThreeMapLoaderSource,
    /timeout:\s*MAP3D_MODULE_LOAD_TIMEOUT_MS/,
  )
  assert.match(
    appThreeMapLoaderSource,
    /}, MAP3D_PRESENTATION_HARD_TIMEOUT_MS\)/,
  )
  assert.doesNotMatch(appThreeMapLoaderSource, /map3dRetry=/)
  assert.match(
    appThreeMapLoaderSource,
    /componentVisible\.value = false[\s\S]*await nextTick\(\)[\s\S]*componentVisible\.value = true/,
  )
  assert.match(appSource, /app-content--map3d-blocked/)
  assert.match(appSource, /map-dimension-toggle \*/)
})
