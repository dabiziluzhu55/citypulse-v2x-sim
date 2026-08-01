<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as mapvthree from '@baidumap/mapv-three'
import { Color } from 'three'
import { useAppMapView } from '../../composables/useAppMapView'
import { useSimulationMap } from '../../composables/useSimulationMap'
import { useSimulationStore } from '../../composables/useSimulationStore'
import { useActiveIntersectionScene } from '../../composables/useActiveIntersectionScene'
import { useCatalog } from '../../composables/useCatalog'
import { BaiduDetailedRoadRenderer } from '../../mapv/BaiduDetailedRoadRenderer'
import { BaiduRoadNetworkRenderer } from '../../mapv/BaiduRoadNetworkRenderer'
import {
  BaiduVehicleRenderer,
  type VehicleRenderStats,
} from '../../mapv/BaiduVehicleRenderer'
import { ShowcaseGeoJsonLayers } from '../../mapv/showcaseLayers/ShowcaseGeoJsonLayers'
import { ShowcaseModelLayers } from '../../mapv/showcaseLayers/ShowcaseModelLayers'
import { RoadsideFacilityRenderer } from '../../mapv/showcaseLayers/RoadsideFacilityRenderer'
import { VegetationRenderer } from '../../mapv/showcaseLayers/VegetationRenderer'
import { MapvRealisticIntersectionLayer } from '../../mapv/realistic/MapvRealisticIntersectionLayer'
import { IntersectionTopologyLayer } from '../../mapv/IntersectionTopologyLayer'
import {
  intersectionTopologyMaxRange,
  intersectionTopologyBounds,
  type IntersectionTopologyNode,
} from '../../mapv/intersectionTopology'
import {
  createIntersectionLaneHeadingResolver,
  createIntersectionLanePoseResolver,
} from '../../mapv/realistic/intersectionLaneHeading'
import {
  loadIntersectionEnvironmentManifest,
  type IntersectionEnvironmentManifest,
} from '../../mapv/realistic/intersectionEnvironmentManifest'
import { parseSceneFacilityManifest } from '../../mapv/showcaseLayers/sceneFacilities'
import {
  BAIDU_DARK_BASE_STYLE,
  createBaiduBaseDisplayOptions,
} from '../../mapv/baiduDarkStyle'
import {
  BAIDU_3D_MAX_RANGE,
  BAIDU_3D_MIN_RANGE,
  DEFAULT_CESIUM_CAMERA_HEIGHT,
} from '../../constants/mapDefaults'
import {
  DEMO_2_SOURCE_CENTER_BD09,
  placeBaiduCameraTarget,
  resolveSimulationCoordinateProjector,
  XIONGAN_SCENE_ANCHOR_BD09,
} from '../../mapv/sceneCoordinates'
import {
  roadTilesetManifestIsValid,
  roadTilesetMatchesResponse,
  type StaticRoadTilesetManifest,
} from '../../mapv/staticRoadTileset'
import type { MapGeoJsonResponse } from '../../types/map'
import { detectMap3dCapability } from '../../mapv/map3dCapabilities'

const emit = defineEmits<{
  fatal: [cause: unknown]
}>()

const ROAD_RENDER_RADIUS_METERS = 900
const containerRef = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const {
  activeIntersectionId,
  sceneStatus,
  sceneError,
  selectionRevision,
  setSceneLoading,
  setSceneReady,
  setSceneError,
} = useActiveIntersectionScene()
const { isIntersectionSupported } = useCatalog(activeIntersectionId)
const { geojson } = useSimulationMap(activeIntersectionId, ROAD_RENDER_RADIUS_METERS, isIntersectionSupported)
const { snapshot, trafficView } = useSimulationStore()

const loading = ref(true)
const error = ref<string | null>(null)
const tilesStatus = ref<'loading' | 'ready' | 'error'>('loading')
const tilesMessage = ref('正在加载百度底图与本地 3D 建筑…')
const interacting = ref(false)
const vehicleStats = ref<VehicleRenderStats>({
  inputCount: 0,
  visibleCount: 0,
  radiusMeters: 420,
})
const showRenderDiagnostics = import.meta.env.DEV

let engine: mapvthree.Engine | null = null
let sky: mapvthree.DefaultSky | null = null
let buildingTileset: mapvthree.Default3DTiles | null = null
let roadTileset: mapvthree.Default3DTiles | null = null
let roadTilesetManifest: StaticRoadTilesetManifest | null = null
let roadTilesReady = false
let buildingTilesReady = false
let buildingStableSamples = 0
let roadRenderer: BaiduRoadNetworkRenderer | BaiduDetailedRoadRenderer | null = null
let vehicleRenderer: BaiduVehicleRenderer | null = null
const showcaseGeoJsonLayers = new Map<string, ShowcaseGeoJsonLayers>()
const showcaseGeoJsonLoading = new Map<string, Promise<void>>()
let showcaseModelLayers: ShowcaseModelLayers | null = null
let roadsideFacilityRenderer: RoadsideFacilityRenderer | null = null
let vegetationRenderer: VegetationRenderer | null = null
let realisticIntersectionLayer: MapvRealisticIntersectionLayer | null = null
let intersectionTopologyLayer: IntersectionTopologyLayer | null = null
let realisticDetailReady = false
let tilesStatusTimer: ReturnType<typeof setInterval> | null = null
let interactionEndTimer: ReturnType<typeof setTimeout> | null = null
let cameraFlightRevision = 0
let cameraFlightActive = false
let lastEmptyVehicleWarningSequence = -25
let sceneSwitchRevision = 0
let documentVisible = typeof document === 'undefined' || !document.hidden

const tilesetUrl =
  import.meta.env.VITE_XIONGAN_3DTILES_URL?.trim()
  || '/3dtiles/xiongan-webmercator/tileset.json'
const enableLocalTileset = import.meta.env.VITE_ENABLE_XIONGAN_3DTILES !== 'false'
const enableStaticRoadTileset = import.meta.env.VITE_ENABLE_STATIC_ROAD_3DTILES !== 'false'
const roadTilesetUrl =
  import.meta.env.VITE_STATIC_ROAD_3DTILES_URL?.trim() || '/3dtiles/roads/demo_2/tileset.json'
const scenePlacement = import.meta.env.VITE_3D_SCENE_PLACEMENT?.trim()
  || 'actual'
const coordinateProjector = resolveSimulationCoordinateProjector(scenePlacement)
const sceneCenter = scenePlacement === 'xiongan-demo'
  ? XIONGAN_SCENE_ANCHOR_BD09
  : DEMO_2_SOURCE_CENTER_BD09
const baiduAk = import.meta.env.VITE_BAIDU_MAP_AK?.trim() || ''
const showBaiduBuildings = false
const showBaiduRoads = import.meta.env.VITE_BAIDU_ROADS !== 'false'
const roadRendererMode = import.meta.env.VITE_BAIDU_ROAD_RENDERER?.trim() || 'detailed'
const enableShowcaseLayers = import.meta.env.VITE_ENABLE_SHOWCASE_LAYERS !== 'false'
const enableJunctionMarkers = import.meta.env.VITE_ENABLE_JUNCTION_MARKERS === 'true'
const enableRoadsideFacilities = import.meta.env.VITE_ENABLE_ROADSIDE_FACILITIES === 'true'
const enableVegetation = import.meta.env.VITE_ENABLE_VEGETATION === 'true'
const enableIntersectionTopology = import.meta.env.VITE_ENABLE_INTERSECTION_TOPOLOGY !== 'false'

const BUILDING_IDLE_ERROR_TARGET = 8
const BUILDING_MOVING_ERROR_TARGET = 24
const BUILDING_CACHE_BYTES = 384 * 1024 * 1024
const buildingZOffsetMeters = Number(import.meta.env.VITE_XIONGAN_BUILDING_Z_OFFSET_METERS ?? 0)
const enableBuildingContactShadows = import.meta.env.VITE_XIONGAN_BUILDING_CONTACT_SHADOWS !== 'false'
const streetlightModelYawOffsetRadians = Number(import.meta.env.VITE_STREETLIGHT_MODEL_YAW_DEGREES ?? 0) * Math.PI / 180
const ACTIVE_FRAME_TIME_MS = 1000 / 60

function createBaiduProvider(): mapvthree.BaiduVectorTileProvider {
  return new mapvthree.BaiduVectorTileProvider({
    ak: baiduAk,
    styleJson: BAIDU_DARK_BASE_STYLE,
    displayOptions: createBaiduBaseDisplayOptions(showBaiduRoads),
    placeholderColor: '#122e2b',
  })
}

function enableCameraInteraction(): void {
  if (!engine) return
  engine.controller.enabled = true
  engine.controller.enableRotate = true
  engine.controller.enableZoom = true
  engine.controller.enablePan = true
  engine.controller.enableTilt = true
  buildingTileset?.releaseCameraViewport()
  roadTileset?.releaseCameraViewport()
}

function markInteracting(): void {
  if (cameraFlightActive) return
  if (!interacting.value) {
    buildingTileset && (buildingTileset.errorTarget = BUILDING_MOVING_ERROR_TARGET)
    vegetationRenderer?.setInteractionActive(true)
    roadsideFacilityRenderer?.refreshViewport()
  }
  interacting.value = true
  enableCameraInteraction()
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  interactionEndTimer = setTimeout(() => {
    interacting.value = false
    if (buildingTileset) buildingTileset.errorTarget = BUILDING_IDLE_ERROR_TARGET
    vegetationRenderer?.setInteractionActive(false)
    roadsideFacilityRenderer?.refreshViewport()
    const stats = vehicleRenderer?.refreshViewport()
    if (stats) updateVehicleRenderStats(stats)
    engine?.requestRender()
    interactionEndTimer = null
  }, 300)
}

function updateVehicleRenderStats(stats: VehicleRenderStats): void {
  vehicleStats.value = stats
  const current = snapshot.value
  if (
    showRenderDiagnostics
    && current?.state === 'RUNNING'
    && (current.metrics.active_vehicles ?? 0) > 0
    && stats.visibleCount === 0
    && current.sequence - lastEmptyVehicleWarningSequence >= 25
  ) {
    lastEmptyVehicleWarningSequence = current.sequence
    console.warn('[vehicle-render] active simulation has no vehicles inside the camera radius', {
      activeVehicles: current.metrics.active_vehicles,
      inputVehicles: stats.inputCount,
      renderRadiusMeters: Math.round(stats.radiusMeters),
      snapshotSequence: current.sequence,
    })
  }
}

function syncAnimationLoop(): void {
  if (!engine) return
  const state = snapshot.value?.state
  const simulationActive = state === 'STARTING' || state === 'RUNNING' || state === 'STOPPING'
  const topologyActive = Boolean(intersectionTopologyLayer?.animationActive)
  const active = documentVisible && (simulationActive || topologyActive)
  engine.rendering.animationLoopFrameTime = simulationActive ? ACTIVE_FRAME_TIME_MS : 1000 / 30
  if (engine.rendering.enableAnimationLoop !== active) {
    engine.rendering.enableAnimationLoop = active
    engine.requestRender()
  }
}

function configureBuildingShadows(tileset: mapvthree.Default3DTiles): void {
  if (!enableBuildingContactShadows) return
  const shadowRoot = tileset as unknown as {
    traverse: (callback: (object: { castShadow?: boolean; receiveShadow?: boolean }) => void) => void
  }
  shadowRoot.traverse((object) => {
    if (typeof object.castShadow === 'boolean') object.castShadow = true
    if (typeof object.receiveShadow === 'boolean') object.receiveShadow = true
  })
}

function updateTilesStatus(): void {
  const activeTilesets = [buildingTileset, roadTileset].filter(
    (value): value is mapvthree.Default3DTiles => value !== null,
  )
  if (activeTilesets.length === 0) return
  const ready = activeTilesets.reduce(
    (sum, value) => sum + value.statistics.numberOfTilesWithContentReady,
    0,
  )
  const pending = activeTilesets.reduce(
    (sum, value) => sum
      + value.statistics.numberOfPendingRequests
      + value.statistics.numberOfTilesProcessing,
    0,
  )
  const loaded = activeTilesets.reduce(
    (sum, value) => sum + value.statistics.numberOfLoadedTilesTotal,
    0,
  )
  const total = activeTilesets.reduce(
    (sum, value) => sum + value.statistics.numberOfTilesTotal,
    0,
  )
  const nextRoadTilesReady = Boolean(
    roadTileset
    && (roadTileset.statistics.numberOfTilesWithContentReady > 0
      || roadTileset.statistics.numberOfLoadedTilesTotal > 0),
  )
  if (nextRoadTilesReady !== roadTilesReady) {
    roadTilesReady = nextRoadTilesReady
    syncRoadRendering(geojson.value)
  }
  const buildingHasContent = Boolean(
    buildingTileset
    && (buildingTileset.statistics.numberOfTilesWithContentReady > 0
      || buildingTileset.statistics.numberOfLoadedTilesTotal > 0),
  )
  const buildingPending = buildingTileset
    ? buildingTileset.statistics.numberOfPendingRequests
      + buildingTileset.statistics.numberOfTilesProcessing
    : 0
  buildingStableSamples = buildingHasContent && buildingPending === 0
    ? buildingStableSamples + 1
    : 0
  const nextBuildingTilesReady = buildingHasContent && buildingStableSamples >= 2
  if (nextBuildingTilesReady !== buildingTilesReady) {
    buildingTilesReady = nextBuildingTilesReady
    if (nextBuildingTilesReady && buildingTileset) configureBuildingShadows(buildingTileset)
    engine?.requestRender()
  }

  const roadSatisfied = realisticDetailReady || !enableStaticRoadTileset || roadTilesReady
  const buildingSatisfied = !enableLocalTileset || buildingTilesReady || showBaiduBuildings
  if (roadSatisfied && buildingSatisfied) {
    tilesStatus.value = 'ready'
    const buildingMessage = buildingTilesReady
      ? '本地建筑已就绪'
      : showBaiduBuildings ? '百度建筑兜底，本地建筑加载中' : '建筑未启用'
    tilesMessage.value = `3D Tiles 已就绪 · ${buildingMessage} · 可见 ${ready} · 已载 ${loaded}${total > 0 ? `/${total}` : ''}`
    return
  }
  tilesStatus.value = 'loading'
  tilesMessage.value = `3D Tiles 加载中 · 请求 ${pending}`
  if (pending > 0) engine?.requestRender()
}

function syncRoadRendering(response: MapGeoJsonResponse | null): void {
  const staticRoadMatches = response
    ? Boolean(
      roadTilesetManifest
      && roadTilesetMatchesResponse(roadTilesetManifest, response, scenePlacement),
    )
    : Boolean(
      roadTilesetManifest
      && roadTilesetManifestIsValid(roadTilesetManifest, scenePlacement),
    )
  if (roadTileset) roadTileset.visible = staticRoadMatches && !realisticDetailReady
  if (realisticDetailReady) {
    roadRenderer?.clear()
    return
  }
  if (response && roadTileset && staticRoadMatches && roadTilesReady && !realisticDetailReady) {
    roadRenderer?.clear()
    return
  }
  roadRenderer?.render(response)
}

async function switchRealisticIntersection(intersectionId: string): Promise<void> {
  if (!realisticIntersectionLayer || !intersectionId) return
  const revision = ++sceneSwitchRevision
  setSceneLoading()
  try {
    const manifest = await realisticIntersectionLayer.prepare(intersectionId)
    if (revision !== sceneSwitchRevision || activeIntersectionId.value !== intersectionId) return
    let cameraReady = false
    let resourcesReady = false
    const completeSwitch = () => {
      if (!cameraReady || !resourcesReady) return
      if (revision !== sceneSwitchRevision || activeIntersectionId.value !== intersectionId) return
      setSceneReady(intersectionId)
    }
    mapView.focusIntersection(
      [manifest.origin.longitude, manifest.origin.latitude],
      manifest.intersectionId,
      {
        force: true,
        duration: 900,
        complete: () => {
          cameraReady = true
          completeSwitch()
        },
      },
    )
    await switchIntersectionEnvironment(intersectionId, revision)
    if (revision !== sceneSwitchRevision || activeIntersectionId.value !== intersectionId) return
    realisticIntersectionLayer.activate(intersectionId)
    realisticDetailReady = true
    vehicleRenderer?.setLaneHeadingResolver(createIntersectionLaneHeadingResolver(manifest))
    vehicleRenderer?.setLanePoseResolver(createIntersectionLanePoseResolver(manifest, coordinateProjector))
    syncRoadRendering(geojson.value)
    roadsideFacilityRenderer?.setRealisticDetailActive(true)
    realisticIntersectionLayer.updateSignals(trafficView.value?.intersections ?? null)
    resourcesReady = true
    completeSwitch()
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') return
    realisticDetailReady = realisticIntersectionLayer.activeIntersectionId !== null
    syncRoadRendering(geojson.value)
    roadsideFacilityRenderer?.setRealisticDetailActive(realisticDetailReady)
    setSceneError(cause instanceof Error ? cause.message : '高精度路口加载失败')
  }
}

function syncMapRendering(response: MapGeoJsonResponse | null): void {
  syncRoadRendering(response)
  showcaseModelLayers?.render(enableJunctionMarkers ? response : null)
}

function applyGlobalNavigationBounds(nodes: IntersectionTopologyNode[]): void {
  if (!engine) return
  const bounds = intersectionTopologyBounds(nodes)
  if (!bounds) return
  const [west, south, east, north] = bounds
  const southwest = coordinateProjector([west, south, 0])
  const northeast = coordinateProjector([east, north, 0])
  engine.map.setBounds([
    [southwest[0], southwest[1]],
    [northeast[0], northeast[1]],
  ])
  const aspectRatio = containerRef.value
    ? containerRef.value.clientWidth / Math.max(1, containerRef.value.clientHeight)
    : 16 / 9
  engine.map.setMaxRange(intersectionTopologyMaxRange(nodes, aspectRatio))
}

function handleDocumentVisibility(): void {
  documentVisible = !document.hidden
  syncAnimationLoop()
}

async function ensureIntersectionLandcover(
  intersectionId: string,
  environment?: IntersectionEnvironmentManifest,
): Promise<void> {
  if (!engine || !enableShowcaseLayers || showcaseGeoJsonLayers.has(intersectionId)) return
  const existing = showcaseGeoJsonLoading.get(intersectionId)
  if (existing) return existing
  const loadingPromise = (async () => {
    const manifest = environment ?? await loadIntersectionEnvironmentManifest(intersectionId)
    if (!manifest.geojson || !engine || showcaseGeoJsonLayers.has(intersectionId)) return
    const layers = new ShowcaseGeoJsonLayers(engine, coordinateProjector)
    try {
      await layers.load(manifest.geojson)
      if (!engine) {
        layers.destroy()
        return
      }
      showcaseGeoJsonLayers.set(intersectionId, layers)
    } catch (cause) {
      layers.destroy()
      throw cause
    }
  })().finally(() => showcaseGeoJsonLoading.delete(intersectionId))
  showcaseGeoJsonLoading.set(intersectionId, loadingPromise)
  return loadingPromise
}

async function prepareAllIntersectionLandcover(intersectionIds: string[]): Promise<void> {
  const queue = [...new Set(intersectionIds)]
  const worker = async () => {
    while (queue.length > 0) {
      const intersectionId = queue.shift()
      if (!intersectionId) return
      await ensureIntersectionLandcover(intersectionId).catch((cause: unknown) => {
        console.warn(`[landcover] ${intersectionId} failed to load`, cause)
      })
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, queue.length) }, worker))
  engine?.requestRender()
}

async function switchIntersectionEnvironment(intersectionId: string, revision: number): Promise<void> {
  const environment = await loadIntersectionEnvironmentManifest(intersectionId)
  const facilitiesPromise = environment.facilitiesUrl && enableRoadsideFacilities
    ? fetch(environment.facilitiesUrl).then(async (response) => {
      if (!response.ok) throw new Error(`Roadside facilities returned HTTP ${response.status}`)
      return parseSceneFacilityManifest(await response.json())
    })
    : Promise.resolve(null)
  const geoJsonPromise = ensureIntersectionLandcover(intersectionId, environment)
  const vegetationPromise = enableVegetation && vegetationRenderer && environment.vegetation
    ? vegetationRenderer.load(
      environment.vegetation.manifestUrl,
      environment.vegetation.modelUrl,
    )
    : Promise.resolve()
  const streetlightPromise = environment.streetlight && roadsideFacilityRenderer
    ? roadsideFacilityRenderer.prepareStreetlight(
      environment.streetlight.modelUrl,
      environment.streetlight.heightMeters,
    )
    : Promise.resolve()
  const detailModelPromise = showcaseModelLayers
    ? showcaseModelLayers.loadLandmark(environment.detailModel ?? null)
    : Promise.resolve()
  const [facilities] = await Promise.all([
    facilitiesPromise,
    geoJsonPromise,
    vegetationPromise,
    streetlightPromise,
    detailModelPromise,
  ])
  if (revision !== sceneSwitchRevision || activeIntersectionId.value !== intersectionId) {
    return
  }
  if (facilities && roadsideFacilityRenderer) {
    roadsideFacilityRenderer.render(facilities)
    roadsideFacilityRenderer.updateSignals(trafficView.value?.intersections ?? null)
  } else {
    roadsideFacilityRenderer?.clearScene()
  }
  if (!environment.vegetation) vegetationRenderer?.clearScene()
}

function createBuildingTileset(url: string): mapvthree.Default3DTiles {
  if (!engine) throw new Error('3D map engine is unavailable')
  const tileset = engine.add(new mapvthree.Default3DTiles({
    url,
    errorTarget: interacting.value
      ? BUILDING_MOVING_ERROR_TARGET
      : BUILDING_IDLE_ERROR_TARGET,
    forceUnlit: false,
    dynamicScreenSpaceError: false,
    foveatedScreenSpaceError: false,
    progressiveResolutionHeightFraction: 1,
    cullRequestsWhileMoving: false,
    cacheBytes: BUILDING_CACHE_BYTES,
  })) as mapvthree.Default3DTiles
  if (Number.isFinite(buildingZOffsetMeters)) tileset.position.z = buildingZOffsetMeters
  tileset.releaseCameraViewport()
  return tileset
}

async function waitForBuildingTiles(tileset: mapvthree.Default3DTiles, timeoutMs = 20_000): Promise<boolean> {
  const startedAt = performance.now()
  let stableSamples = 0
  let hasContent = false
  while (performance.now() - startedAt < timeoutMs) {
    if (!engine || buildingTileset !== tileset) return false
    hasContent = (
      tileset.statistics.numberOfTilesWithContentReady > 0
      || tileset.statistics.numberOfLoadedTilesTotal > 0
    )
    const pending = tileset.statistics.numberOfPendingRequests
      + tileset.statistics.numberOfTilesProcessing
    stableSamples = hasContent && pending === 0 ? stableSamples + 1 : 0
    if (stableSamples >= 3) return true
    engine.requestRender()
    await new Promise((resolve) => setTimeout(resolve, 120))
  }
  return hasContent
}

async function addGlobalBuildingTileset(): Promise<void> {
  if (!engine || !enableLocalTileset || buildingTileset) return
  tilesStatus.value = 'loading'
  buildingTileset = createBuildingTileset(tilesetUrl)
  const ready = await waitForBuildingTiles(buildingTileset)
  if (!ready) throw new Error(`Global building tileset did not become ready: ${tilesetUrl}`)
  buildingTilesReady = true
  updateTilesStatus()
  engine.requestRender()
}

async function addStaticRoadTileset(): Promise<void> {
  if (!engine || !enableStaticRoadTileset) return
  const manifestUrl = new URL(
    './manifest.json',
    new URL(roadTilesetUrl, window.location.href),
  )
  const response = await fetch(manifestUrl)
  if (!response.ok) throw new Error(`道路 3D Tiles manifest 加载失败 (${response.status})`)
  roadTilesetManifest = await response.json() as StaticRoadTilesetManifest
  roadTileset = engine.add(new mapvthree.Default3DTiles({
    url: roadTilesetUrl,
    errorTarget: 8,
    forceUnlit: true,
    dynamicScreenSpaceError: false,
    foveatedScreenSpaceError: false,
    cullRequestsWhileMoving: true,
    cullRequestsWhileMovingMultiplier: 120,
    cacheBytes: 32 * 1024 * 1024,
  })) as mapvthree.Default3DTiles
  roadTileset.releaseCameraViewport()
}

function bindContainerInteraction(container: HTMLElement): void {
  container.addEventListener('pointerdown', markInteracting, { passive: true })
  container.addEventListener('pointermove', markInteracting, { passive: true })
  container.addEventListener('wheel', markInteracting, { passive: true })
}

function unbindContainerInteraction(container: HTMLElement | null): void {
  if (!container) return
  container.removeEventListener('pointerdown', markInteracting)
  container.removeEventListener('pointermove', markInteracting)
  container.removeEventListener('wheel', markInteracting)
}

function handleWebglContextLost(event: Event): void {
  event.preventDefault()
  error.value = '三维图形上下文暂时不可用，正在恢复'
  loading.value = true
}

function handleWebglContextRestored(): void {
  error.value = null
  loading.value = false
  engine?.requestRender()
  void switchRealisticIntersection(activeIntersectionId.value)
}

async function initMap(): Promise<void> {
  const container = containerRef.value
  if (!container) return
  const capability = detectMap3dCapability()
  if (!capability.supported) {
    emit('fatal', new Error(capability.reason ?? '当前浏览器不支持三维地图'))
    mapView.setDimension('2d')
    return
  }
  if (!baiduAk) {
    throw new Error('未配置 VITE_BAIDU_MAP_AK，请先填写百度地图浏览器端 AK')
  }

  mapvthree.BaiduMapConfig.ak = baiduAk
  engine = new mapvthree.Engine(container, {
    map: {
      projection: mapvthree.PROJECTION_WEB_MERCATOR,
      center: sceneCenter,
      pitch: 58,
      heading: 30,
      range: DEFAULT_CESIUM_CAMERA_HEIGHT,
      is3DControl: true,
      provider: createBaiduProvider(),
    },
    rendering: {
      sky: null,
      enableAnimationLoop: false,
      animationLoopFrameTime: ACTIVE_FRAME_TIME_MS,
    },
  })
  engine.map.setMinRange(BAIDU_3D_MIN_RANGE)
  engine.map.setMaxRange(BAIDU_3D_MAX_RANGE)
  const qualityEngine = engine as unknown as {
    rendering: { features?: { bloom?: { enabled: boolean } } }
    clock?: { _setTimeLegacy?: (seconds: number) => void }
  }
  if (qualityEngine.rendering.features?.bloom) {
    qualityEngine.rendering.features.bloom.enabled = true
  }
  qualityEngine.clock?._setTimeLegacy?.(14.5 * 3600)
  sky = engine.add(new mapvthree.DefaultSky())
  sky.color = new Color('#152535')
  sky.highColor = new Color('#07111d')
  sky.skyLightIntensity = 1.3
  ;(sky as unknown as { sunIntensityScale: number }).sunIntensityScale = 0.65
  enableCameraInteraction()
  bindContainerInteraction(container)
  container.addEventListener('webglcontextlost', handleWebglContextLost, true)
  container.addEventListener('webglcontextrestored', handleWebglContextRestored, true)

  if (enableLocalTileset) {
    tilesMessage.value = '3D Tiles 已加入场景，正在加载可见建筑…'
    await addGlobalBuildingTileset()
  } else {
    tilesStatus.value = 'ready'
    tilesMessage.value = showBaiduBuildings
      ? '已启用百度地图 3D 建筑 · 本地 3D Tiles 暂停加载'
      : '本地 3D Tiles 暂停加载 · 百度建筑已关闭'
  }

  await addStaticRoadTileset().catch((cause: unknown) => {
    roadTilesetManifest = null
    roadTileset = null
    console.warn(cause instanceof Error ? cause.message : cause)
  })
  if (enableLocalTileset || roadTileset) {
    tilesStatusTimer = setInterval(updateTilesStatus, 500)
  }

  roadRenderer = roadRendererMode === 'basic'
    ? new BaiduRoadNetworkRenderer(engine, coordinateProjector)
    : new BaiduDetailedRoadRenderer(engine, coordinateProjector)
  vehicleRenderer = new BaiduVehicleRenderer(engine, coordinateProjector)
  realisticIntersectionLayer = new MapvRealisticIntersectionLayer(engine, coordinateProjector)
  if (enableIntersectionTopology) {
    intersectionTopologyLayer = new IntersectionTopologyLayer(engine, coordinateProjector)
    void intersectionTopologyLayer.load().then((nodes) => {
      applyGlobalNavigationBounds(nodes)
      void prepareAllIntersectionLandcover(nodes.map((node) => node.intersectionId))
      void realisticIntersectionLayer?.prepareOverview(nodes.map((node) => node.intersectionId))
        .catch((cause: unknown) => console.warn('[intersection-overview] failed to load', cause))
      intersectionTopologyLayer?.setActiveIntersection(activeIntersectionId.value)
      syncAnimationLoop()
    }).catch((cause: unknown) => {
      console.warn('[intersection-topology] failed to load', cause)
    })
  }
  if (enableShowcaseLayers) {
    showcaseModelLayers = new ShowcaseModelLayers(engine, coordinateProjector)
  }
  if (enableRoadsideFacilities) {
    roadsideFacilityRenderer = new RoadsideFacilityRenderer(
      engine,
      coordinateProjector,
      Number.isFinite(streetlightModelYawOffsetRadians) ? streetlightModelYawOffsetRadians : 0,
    )
  }
  if (enableVegetation) {
    vegetationRenderer = new VegetationRenderer(engine, coordinateProjector)
    vegetationRenderer.setInteractionActive(interacting.value)
  }
  watch(
    trafficView,
    (value) => {
      const current = snapshot.value
      const stats = vehicleRenderer?.update(value?.vehicles ?? [], {
        sessionId: current?.session_id ?? '',
        state: current?.state ?? null,
        sequence: current?.sequence ?? -1,
        elapsedSeconds: current?.elapsed_seconds ?? 0,
      })
      if (stats) updateVehicleRenderStats(stats)
      roadsideFacilityRenderer?.updateSignals(value?.intersections ?? null)
      realisticIntersectionLayer?.updateSignals(value?.intersections ?? null)
    },
    { immediate: true },
  )
  watch(
    [activeIntersectionId, selectionRevision],
    ([intersectionId]) => {
      intersectionTopologyLayer?.setActiveIntersection(intersectionId)
      void switchRealisticIntersection(intersectionId)
    },
    { immediate: true },
  )
  watch(snapshot, syncAnimationLoop, { immediate: true })
  watch(
    geojson,
    syncMapRendering,
    { immediate: true },
  )

  mapView.registerThreeMap({
    flyTo: (target, options) => {
      if (options.force || !interacting.value) {
        const revision = ++cameraFlightRevision
        cameraFlightActive = options.duration > 0
        if (cameraFlightActive && engine) engine.controller.enabled = false
        engine?.map.flyTo(placeBaiduCameraTarget(target, scenePlacement), {
          ...options,
          complete: () => {
            if (revision !== cameraFlightRevision) return
            cameraFlightActive = false
            enableCameraInteraction()
            options.complete()
          },
        })
      }
    },
    setViewport: (points, options) => {
      if (options.force || !interacting.value) {
        engine?.map.setViewport(
          points.map((point) => placeBaiduCameraTarget(point, scenePlacement)),
          options,
        )
      }
    },
    setRangeLimits: (minimum, maximum) => {
      engine?.map.setMinRange(minimum)
      engine?.map.setMaxRange(maximum)
    },
  })
  engine.requestRender()
  loading.value = false
}

onMounted(() => {
  document.addEventListener('visibilitychange', handleDocumentVisibility)
  void initMap().catch((cause: unknown) => {
    const failure = cause instanceof Error ? cause : new Error('百度三维地图初始化失败')
    error.value = failure.message
    tilesStatus.value = 'error'
    tilesMessage.value = error.value
    loading.value = false
    emit('fatal', failure)
  })
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', handleDocumentVisibility)
  mapView.unregisterThreeMap()
  unbindContainerInteraction(containerRef.value)
  containerRef.value?.removeEventListener('webglcontextlost', handleWebglContextLost, true)
  containerRef.value?.removeEventListener('webglcontextrestored', handleWebglContextRestored, true)
  if (tilesStatusTimer) clearInterval(tilesStatusTimer)
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  tilesStatusTimer = null
  interactionEndTimer = null
  roadRenderer?.destroy()
  roadRenderer = null
  vehicleRenderer?.destroy()
  vehicleRenderer = null
  showcaseGeoJsonLayers.forEach((layers) => layers.destroy())
  showcaseGeoJsonLayers.clear()
  showcaseGeoJsonLoading.clear()
  showcaseModelLayers?.destroy()
  showcaseModelLayers = null
  roadsideFacilityRenderer?.destroy()
  roadsideFacilityRenderer = null
  vegetationRenderer?.destroy()
  vegetationRenderer = null
  realisticIntersectionLayer?.destroy()
  realisticIntersectionLayer = null
  intersectionTopologyLayer?.destroy()
  intersectionTopologyLayer = null
  realisticDetailReady = false
  if (sky && engine) engine.remove(sky)
  sky = null
  if (buildingTileset && engine) engine.remove(buildingTileset)
  if (roadTileset && engine) engine.remove(roadTileset)
  buildingTileset = null
  roadTileset = null
  roadTilesetManifest = null
  buildingTilesReady = false
  buildingStableSamples = 0
  roadTilesReady = false
  engine?.dispose()
  engine = null
})
</script>

<template>
  <div class="app-baidu-three-map">
    <div ref="containerRef" class="app-baidu-three-map__canvas" />
    <div v-if="loading" class="app-baidu-three-map__overlay">正在加载百度底图与本地 3D 建筑…</div>
    <div
      v-else-if="error"
      class="app-baidu-three-map__overlay app-baidu-three-map__overlay--error"
    >
      {{ error }}
    </div>
    <div v-if="tilesStatus !== 'ready'" class="app-baidu-three-map__status" :class="`is-${tilesStatus}`">
      <span class="app-baidu-three-map__status-dot" />
      <span>{{ tilesMessage }}</span>
      <span v-if="interacting"> · 自由视角</span>
    </div>
    <div v-if="sceneStatus === 'loading' || sceneStatus === 'error'" class="app-baidu-three-map__detail-status" :class="`is-${sceneStatus}`">
      <span class="app-baidu-three-map__status-dot" />
      <span v-if="sceneStatus === 'loading'">正在加载 {{ activeIntersectionId }} 高精度路口</span>
      <span v-else-if="sceneStatus === 'error'">{{ sceneError }}</span>
    </div>
  </div>
</template>

<style scoped>
.app-baidu-three-map {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  background: #0d1b2a;
}

.app-baidu-three-map__canvas {
  width: 100%;
  height: 100%;
  touch-action: none;
  pointer-events: auto;
}

.app-baidu-three-map__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(2, 10, 24, 0.72);
  color: #9edfff;
  font-size: 14px;
  text-align: center;
  pointer-events: none;
}

.app-baidu-three-map__overlay--error {
  color: #ffb4b4;
}

.app-baidu-three-map__status {
  position: absolute;
  right: calc(var(--dashboard-panel-inset-right, 30px) + 610px);
  bottom: 28px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid rgba(33, 230, 255, 0.24);
  border-radius: 999px;
  background: rgba(2, 10, 24, 0.78);
  color: #9edfff;
  font-size: 11px;
  pointer-events: none;
}

.app-baidu-three-map__detail-status {
  position: absolute;
  left: 50%;
  bottom: 28px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 10px;
  border: 1px solid rgba(33, 230, 255, 0.26);
  border-radius: 4px;
  background: rgba(2, 10, 24, 0.82);
  color: #9edfff;
  font-size: 11px;
  transform: translateX(-50%);
  pointer-events: none;
}

.app-baidu-three-map__detail-status.is-ready .app-baidu-three-map__status-dot {
  background: #3ce69a;
}

.app-baidu-three-map__detail-status.is-error {
  color: #ffb4b4;
}

.app-baidu-three-map__status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e8b94c;
  box-shadow: 0 0 7px currentColor;
}

.app-baidu-three-map__status.is-ready .app-baidu-three-map__status-dot {
  background: #3ce69a;
}

.app-baidu-three-map__status.is-error {
  color: #ffb4b4;
}

.app-baidu-three-map__status.is-error .app-baidu-three-map__status-dot {
  background: #ff6b6b;
}

@media (max-width: 1320px) {
  .app-baidu-three-map__status {
    right: 18px;
    bottom: 18px;
  }
}
</style>
