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
const backgroundMapSource = await readFile(
  new URL('../src/components/visualization/AppBackgroundMap.vue', import.meta.url),
  'utf8',
)
const cesiumMapSource = await readFile(
  new URL('../src/components/visualization/CesiumMap.vue', import.meta.url),
  'utf8',
)
const simulationStoreSource = await readFile(
  new URL('../src/composables/useSimulationStore.ts', import.meta.url),
  'utf8',
)
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

  const recentlyProgressed = createBuildingLoadTracker(
  MAP3D_PRESENTATION_HARD_TIMEOUT_MS - MAP3D_STALL_WINDOW_MS / 2,
  1,
)
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
  assert.equal(MAP3D_MODULE_LOAD_TIMEOUT_MS, 60_000)
  assert.equal(MAP3D_PRESENTATION_HARD_TIMEOUT_MS, 90_000)
  assert.equal(MAP3D_STALL_WINDOW_MS, 30_000)
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

test('keeps the 3D loading spinner moving when reduced motion is requested', () => {
  const reducedMotionBlock = appThreeMapLoaderSource.slice(
    appThreeMapLoaderSource.indexOf('@media (prefers-reduced-motion: reduce)'),
  )
  assert.match(reducedMotionBlock, /app-three-map-loader__spinner[\s\S]*animation-duration:\s*1\.2s/)
  assert.doesNotMatch(reducedMotionBlock, /app-three-map-loader__spinner[\s\S]*animation:\s*none/)
})

test('mounts 3D lazily, preserves its engine, and releases the hidden 2D map', () => {
  assert.match(appSource, /const threeMapMounted = ref\(false\)/)
  assert.match(
    appSource,
    /watch\(mapDimension,[\s\S]*dimension === '3d'[\s\S]*threeMapMounted\.value = true/,
  )
  assert.match(
    appSource,
    /<AppBackgroundMap v-if="map2dMounted" :active="map2dActive"/,
  )
  assert.match(
    appSource,
    /<AppThreeMapLoader\s*:active="map3dActive"/,
  )
  assert.match(appSource, /await waitForRenderFrame\(\)[\s\S]*await waitForRenderFrame\(\)/)
  assert.match(appSource, /transition: opacity 120ms linear/)
  assert.match(baiduThreeMapSource, /vehicleRenderer\?\.setActive\(active\)/)
  assert.match(baiduThreeMapSource, /props\.active && documentVisible/)
  assert.match(backgroundMapSource, /const vehicleFeatures = new globalThis\.Map/)
  assert.match(appSource, /threeMapState\.value === 'ready'[\s\S]*map2dMounted\.value = false/)
  assert.match(backgroundMapSource, /releaseFullRoadNetwork\(\)/)
  assert.doesNotMatch(backgroundMapSource, /function renderVehicles\(\) \{\s*vehicleSource\.clear\(\)/)
})

test('resizes the on-demand 3D renderer without restoring a permanent frame loop', () => {
  assert.match(baiduThreeMapSource, /new ResizeObserver\(scheduleEngineResize\)/)
  assert.match(
    baiduThreeMapSource,
    /rendering\.resolution = new Vector2\(width, height\)[\s\S]*activeEngine\.requestRender\(\)/,
  )
  assert.match(baiduThreeMapSource, /overlayViewToken\.value \+= 1/)
  assert.match(baiduThreeMapSource, /stopEngineResizeObserver\(\)/)
  assert.doesNotMatch(baiduThreeMapSource, /engineResizeObserver[\s\S]*enableAnimationLoop = true/)
})

test('clears 2D and 3D vehicle state exactly when a new backend session is accepted', () => {
  const bindSessionBlock = simulationStoreSource.slice(
    simulationStoreSource.indexOf('function bindSession'),
    simulationStoreSource.indexOf('function ensureInitialized'),
  )
  assert.match(bindSessionBlock, /nextSessionId && nextSessionId !== sessionId\.value/)
  assert.match(bindSessionBlock, /renderSessionRevision\.value \+= 1/)
  assert.match(backgroundMapSource, /watch\(renderSessionRevision, clearSessionPresentation, \{ flush: 'sync' \}\)/)
  assert.match(baiduThreeMapSource, /watch\([\s\S]*renderSessionRevision[\s\S]*vehicleRenderer\?\.clear\(\)/)
  assert.match(baiduThreeMapSource, /sceneEventMarkerLayer\?\.setMarkers\(\[\]\)/)
  assert.match(baiduThreeMapSource, /realisticIntersectionLayer\?\.updateRuntimeDisturbances\(\[\]\)/)
  assert.match(cesiumMapSource, /renderSessionRevision[\s\S]*vehicleRenderer\?\.clear\(\)/)
  assert.doesNotMatch(bindSessionBlock, /TERMINAL_SIMULATION_STATES/)
})

test('installs accepted-session targets only after the old session render boundary is cleared', () => {
  const launchBlock = simulationStoreSource.slice(
    simulationStoreSource.indexOf('async function launchRun'),
    simulationStoreSource.indexOf('function clearStatusError'),
  )
  const bindSessionBlock = simulationStoreSource.slice(
    simulationStoreSource.indexOf('function bindSession'),
    simulationStoreSource.indexOf('function ensureInitialized'),
  )
  assert.doesNotMatch(launchBlock, /setRuntimeDisturbanceTargets\(result\.session_id/)
  assert.match(launchBlock, /bindSession\([\s\S]*\}, payload\)/)
  assert.ok(
    bindSessionBlock.indexOf('renderSessionRevision.value += 1')
      < bindSessionBlock.indexOf('setRuntimeDisturbanceTargets(nextSessionId, runtimePayload)'),
  )
  assert.ok(
    bindSessionBlock.indexOf('snapshot.value = null')
      < bindSessionBlock.indexOf('setRuntimeDisturbanceTargets(nextSessionId, runtimePayload)'),
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

test('rebuilds once after WebGL loss and uses a reduced recovery budget', () => {
  assert.match(baiduThreeMapSource, /failure\.name = 'WebGLContextLostError'/)
  assert.match(baiduThreeMapSource, /sceneSwitchCoordinator\.cancel\(\)/)
  assert.match(baiduThreeMapSource, /vehicleRenderer\?\.setActive\(false\)/)
  assert.match(appThreeMapLoaderSource, /shouldAutomaticallyRecoverWebgl/)
  assert.match(appThreeMapLoaderSource, /void remountThreeMap\(true\)/)
  assert.match(appThreeMapLoaderSource, /if \(fatalFailureLatched\) return/)
  assert.match(appThreeMapLoaderSource, /fatalFailureLatched = true[\s\S]*reportFailure\(cause\)/)
  assert.match(appThreeMapLoaderSource, /:recovery-mode="recoveryMode"/)
})
