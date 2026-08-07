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
  createBuildingLoadTracker,
  FINAL_RENDER_FRAME_COUNT,
  MAP3D_MODULE_LOAD_TIMEOUT_MS,
  MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
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
const indexSource = await readFile(new URL('../index.html', import.meta.url), 'utf8')
const mainSource = await readFile(new URL('../src/main.ts', import.meta.url), 'utf8')

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

test('opens with enough stable real tiles even when diagnostic coverage is low', () => {
  const tracker = advanceSamples(
    createBuildingLoadTracker(1_000, 1),
    sample({ readyTiles: 659, pendingRequests: 318 }),
    'full',
    BUILDING_STABLE_SAMPLE_COUNT + 1,
  )

  assert.equal(tracker.activeRequests, 318)
  assert.ok(tracker.coverage < 0.7)
  assert.equal(buildingPresentationUsable(tracker), true)
  assert.equal(buildingPresentationSettled(tracker), false)
})

test('keeps scheduler attempts in demand without reporting them as active requests', () => {
  const tracker = advanceBuildingLoadTracker(
    createBuildingLoadTracker(1_000, 1),
    sample({
      readyTiles: 0,
      pendingRequests: 18,
      processingTiles: 2,
      attemptedRequests: 945,
    }),
    'full',
  )

  assert.equal(tracker.activeRequests, 20)
  assert.equal(tracker.demandedTiles, 965)
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

test('uses the same real-tile presentation threshold on all WebGL quality levels', () => {
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

  assert.equal(buildingPresentationUsable(full), true)
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

test('only treats a zero-content load with no progress as stalled', () => {
  const partial = advanceBuildingLoadTracker(createBuildingLoadTracker(0, 1), sample({
    readyTiles: 200,
    pendingRequests: 30,
    nowMs: 1_000,
  }), 'full')
  assert.equal(buildingLoadStalled(partial, 1_000 + MAP3D_STALL_WINDOW_MS), false)

  const empty = createBuildingLoadTracker(0, 1)
  assert.equal(buildingLoadStalled(empty, MAP3D_STALL_WINDOW_MS), true)
})

test('uses the scene deadline as a partial-building fallback without hiding core failures', () => {
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
    createBuildingLoadTracker(MAP3D_PRESENTATION_HARD_TIMEOUT_MS, 1),
    sample({
      readyTiles: 200,
      pendingRequests: 30,
      nowMs: MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
    }),
    'full',
  )
  assert.equal(
    resolveMap3dPresentationDecision(
      signals,
      progressing,
      MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
      MAP3D_PRESENTATION_HARD_TIMEOUT_MS + 1,
    ),
    'present',
  )

  const recentlyProgressed = createBuildingLoadTracker(55_000, 1)
  assert.equal(
    resolveMap3dPresentationDecision(
      { ...signals, buildingReadyTiles: 0, buildingCoverage: 0 },
      recentlyProgressed,
      MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
      MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
    ),
    'wait',
  )
  assert.equal(
    resolveMap3dPresentationDecision(
      { ...signals, providerReady: false, buildingReadyTiles: 0, buildingCoverage: 0 },
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

test('keeps module, scene deadline, stability, and stall timing separate', () => {
  assert.equal(MAP3D_MODULE_LOAD_TIMEOUT_MS, 15_000)
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
  assert.match(baiduThreeMapSource, /cullRequestsWhileMoving:\s*false/)
  assert.match(
    baiduThreeMapSource,
    /presentationReady = true\s+if \(buildingTileset\) buildingTileset\.cullRequestsWhileMoving = true/,
  )
  assert.match(baiduThreeMapSource, /await fetch\(tilesetUrl\)/)
  assert.match(baiduThreeMapSource, /缺少 asset 或 root/)
})

test('keeps one module timeout, delegates scene readiness, and tears down retries', () => {
  assert.match(
    appThreeMapLoaderSource,
    /timeout:\s*MAP3D_MODULE_LOAD_TIMEOUT_MS/,
  )
  assert.doesNotMatch(appThreeMapLoaderSource, /MAP3D_PRESENTATION_HARD_TIMEOUT_MS/)
  assert.doesNotMatch(appThreeMapLoaderSource, /Date\.now\(\).*BaiduThreeMap|map3dCacheRecovery/)
  assert.match(appThreeMapLoaderSource, /const MAX_AUTO_RETRIES = 1/)
  assert.match(
    appThreeMapLoaderSource,
    /componentVisible\.value = false[\s\S]*await nextTick\(\)[\s\S]*componentVisible\.value = true/,
  )
  assert.match(
    appThreeMapLoaderSource,
    /function handleLoading[\s\S]*failure\.value = null[\s\S]*state\.value = 'loading'/,
  )
  assert.match(
    appThreeMapLoaderSource,
    /function handleReady[\s\S]*failure\.value = null[\s\S]*state\.value = 'ready'/,
  )
  assert.match(appSource, /app-content--map3d-blocked/)
  assert.match(appSource, /map-dimension-toggle \*/)
})

test('mounts 3D lazily and preserves the engine across 2D/3D view switches', () => {
  assert.match(appSource, /const threeMapMounted = ref\(false\)/)
  assert.match(
    appSource,
    /watch\(mapDimension,[\s\S]*dimension === '3d'[\s\S]*threeMapMounted\.value = true/,
  )
  assert.match(
    appSource,
    /<div v-show="mapDimension === '2d'" class="app-map-layer">\s*<AppBackgroundMap/,
  )
  assert.match(
    appSource,
    /<div v-if="threeMapMounted" v-show="mapDimension === '3d'" class="app-map-layer">\s*<AppThreeMapLoader/,
  )
})

test('provides a black pre-mount crash shell and clears it only after Vue mounts', () => {
  assert.match(indexSource, /id="app-startup-shell"/)
  assert.match(indexSource, /background:\s*#000/)
  assert.match(indexSource, /应用程序启动失败/)
  assert.match(indexSource, /应用入口加载超过 15 秒/)
  assert.match(indexSource, /sessionStorage\.getItem\(RETRY_KEY\) !== '1'/)
  assert.match(indexSource, /重新加载应用/)
  assert.match(mainSource, /window\.__CITYPULSE_STARTUP__\?\.mounted\(\)/)
})

test('re-enters the black gate for WebGL loss and fails if recovery never arrives', () => {
  assert.match(baiduThreeMapSource, /emit\('loading', '三维图形上下文已丢失，正在恢复'\)/)
  assert.match(baiduThreeMapSource, /三维图形上下文在 10 秒内未能恢复/)
  assert.match(baiduThreeMapSource, /if \(webglRecoveryTimer\) clearTimeout\(webglRecoveryTimer\)/)
})
