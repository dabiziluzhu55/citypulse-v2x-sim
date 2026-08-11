<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as mapvthree from '@baidumap/mapv-three'
import { Color, Vector2, Vector3 } from 'three'
import DetectedEventOverlay from './DetectedEventOverlay.vue'
import RuntimeDisturbanceOverlay from './RuntimeDisturbanceOverlay.vue'
import { REALISTIC_INTERSECTION_SURFACE_Z } from '../../mapv/sceneElevation'
import { activeDetectedEventCards } from '../../utils/detectedEventDisplay'
import {
  buildDirectedRouteCongestionLevels,
  loadEdgeTopologySegmentMap,
} from '../../utils/edgeTopologySegments'
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
import {
  MapvRealisticIntersectionLayer,
  type RealisticRuntimeDisturbance,
} from '../../mapv/realistic/MapvRealisticIntersectionLayer'
import type { RealisticIntersectionManifest } from '../../mapv/realistic/intersectionManifest'
import {
  fullyExcludedSurfaceEdgeIds,
  surfaceVisibilityIntervals,
} from '../../mapv/realistic/roadSurfaceExclusions'
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
  unprojectWebMercatorToBd09,
  XIONGAN_SCENE_ANCHOR_BD09,
} from '../../mapv/sceneCoordinates'
import {
  roadTilesetManifestIsValid,
  roadTilesetMatchesResponse,
  type StaticRoadTilesetManifest,
} from '../../mapv/staticRoadTileset'
import type { MapGeoJsonResponse } from '../../types/map'
import {
  recordSimulationLongTasks,
  recordVehicleRuntimeDiagnostics,
} from '../../utils/simulationRuntimeDiagnostics'
import {
  MAP3D_NORMAL_FRAME_RATE,
  MAP3D_STABLE_FRAME_RATE,
  Map3dPerformanceGovernor,
} from '../../mapv/map3dPerformanceGovernor'
import { runtimeDisturbanceLaneIds } from '../../utils/runtimeDisturbances'
import { SceneSwitchCoordinator } from '../../utils/sceneSwitchCoordinator'
import { detectMap3dCapability } from '../../mapv/map3dCapabilities'
import {
  cameraFlightWatchdogDelay,
  createCameraFlightGuard,
  type CameraFlightGuard,
} from '../../utils/cameraFlightGuard'
import {
  assertGlobalBuildingSource,
  buildingTilesetManifestUrl,
} from '../../mapv/buildingTilesetSource'
import {
  advanceBuildingLoadTracker,
  buildingPresentationSettled,
  buildingPresentationUsable,
  BUILDING_STABLE_SAMPLE_INTERVAL_MS,
  createBuildingLoadTracker,
  FINAL_RENDER_FRAME_COUNT,
  MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
  map3dLoadingStage,
  resolveMap3dPresentationDecision,
  type BuildingLoadTracker,
  type Map3dPresentationSignals,
  type Map3dRenderQuality,
} from '../../mapv/map3dPresentationReadiness'

const emit = defineEmits<{
  fatal: [cause: unknown]
  loading: [message: string]
  ready: []
}>()
const props = withDefaults(defineProps<{
  active?: boolean
  recoveryMode?: boolean
}>(), {
  active: true,
  recoveryMode: false,
})

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
const {
  snapshot,
  trafficView,
  renderSessionRevision,
  runtimeDisturbances,
  unmappedRuntimeEvents,
} = useSimulationStore()
const topologyNodes = ref<IntersectionTopologyNode[]>([])
const runtimeDisturbanceMarkers = computed(() => runtimeDisturbances.value.flatMap((event) => {
  const node = topologyNodes.value.find((candidate) => candidate.intersectionId === event.intersectionId)
  return node ? [{ ...event, longitude: node.longitude, latitude: node.latitude }] : []
}))
const overlayViewToken = ref(0)

const detectedEventCards = computed(() => (
  activeDetectedEventCards(snapshot.value?.event_detection?.cards)
))
const detectedOverlayActive = computed(() => {
  const state = snapshot.value?.state
  return state === 'RUNNING' || state === 'PAUSED' || state === 'STARTING' || state === 'STOPPING'
})

const loading = ref(true)
const error = ref<string | null>(null)
const tilesStatus = ref<'loading' | 'refining' | 'ready' | 'error'>('loading')
const tilesMessage = ref('正在加载百度底图与本地 3D 建筑…')
const interacting = ref(false)
const vehicleStats = ref<VehicleRenderStats>({
  inputCount: 0,
  visibleCount: 0,
  radiusMeters: 420,
  vehicleLimit: 450,
  quality: 'full',
  fps: null,
  bufferSeconds: 0.5,
  sourceRate: 1,
  sourceGapP95Ms: 0,
  sourceGapP99Ms: 0,
  underrunCount: 0,
  underrunActive: false,
  laneRecoveryCount: 0,
  temporarilyHiddenCount: 0,
  retainedMissingCount: 0,
  confirmedRemovedCount: 0,
  twinResetCount: 0,
  twinSafetyMarginMs: 250,
  maximumTwinOutputGapMs: 0,
  emptyBufferInterceptCount: 0,
  terminalFreezeActive: false,
  laneRecoveryVehicleIds: [],
  temporarilyHiddenVehicleIds: [],
  duplicateVehicleIds: [],
  incompatiblePathInterpolationCount: 0,
  poseViolationCount: 0,
  targetBufferSeconds: 1.5,
  expectedPlaybackRate: 1,
  predictedVehicleCount: 0,
  maximumPredictionSeconds: 0,
  reconcilingVehicleCount: 0,
  maximumReconciliationSpeedMetersPerSecond: 0,
  maximumReconciliationAccelerationMetersPerSecondSquared: 0,
  movingFreezeFrameCount: 0,
  batchArrivalCount: 0,
  twinGapFillFrameCount: 0,
  twinPlaybackBacklogMs: 0,
  vehicleScaleViolationCount: 0,
  normalTransitionEpochViolationCount: 0,
  vehiclePoseDiagnostics: [],
})
const vehicleBufferBusy = computed(() => {
  const current = snapshot.value
  if (current?.state !== 'RUNNING' || vehicleStats.value.sourceGapP95Ms <= 0) return false
  const targetRate = current.playback_speed ?? 1
  return vehicleStats.value.underrunActive
    || vehicleStats.value.quality === 'constrained'
    || vehicleStats.value.sourceRate < targetRate * 0.75
})
const showRenderDiagnostics = import.meta.env.DEV
const stableRenderMode = ref(props.recoveryMode)
const performanceGovernor = new Map3dPerformanceGovernor(props.recoveryMode)

let engine: mapvthree.Engine | null = null
let baiduProvider: mapvthree.BaiduVectorTileProvider | null = null
let sky: mapvthree.DefaultSky | null = null
let buildingTileset: mapvthree.Default3DTiles | null = null
let roadTileset: mapvthree.Default3DTiles | null = null
let roadTilesetManifest: StaticRoadTilesetManifest | null = null
let roadTilesReady = false
let buildingTilesReady = false
let buildingLoadTracker: BuildingLoadTracker = createBuildingLoadTracker()
let buildingCameraRevision = 0
let renderQuality: Map3dRenderQuality = 'full'
let presentationStartedAt = 0
let roadRenderer: BaiduRoadNetworkRenderer | BaiduDetailedRoadRenderer | null = null
let vehicleRenderer: BaiduVehicleRenderer | null = null
const showcaseGeoJsonLayers = new Map<string, ShowcaseGeoJsonLayers>()
const showcaseGeoJsonLoading = new Map<string, {
  promise: Promise<void>
  signal?: AbortSignal
}>()
const showcaseGeoJsonUsedAt = new Map<string, number>()
let showcaseModelLayers: ShowcaseModelLayers | null = null
let roadsideFacilityRenderer: RoadsideFacilityRenderer | null = null
let vegetationRenderer: VegetationRenderer | null = null
let realisticIntersectionLayer: MapvRealisticIntersectionLayer | null = null
let intersectionTopologyLayer: IntersectionTopologyLayer | null = null
let realisticDetailReady = false
let tilesStatusTimer: ReturnType<typeof setInterval> | null = null
let interactionEndTimer: ReturnType<typeof setTimeout> | null = null
let roadLodIdleHandle: number | null = null
let roadLodIdleUsesTimeout = false
let engineResizeObserver: ResizeObserver | null = null
let engineResizeFrameId: number | null = null
let cameraFlightRevision = 0
let cameraFlightActive = false
let cameraFlightGuard: CameraFlightGuard | null = null
let lastRoadLodRefreshAt = 0
let lastEmptyVehicleWarningSequence = -25
let lastVehicleUnderrunCount = 0
let sceneSwitchRevision = 0
const sceneSwitchCoordinator = new SceneSwitchCoordinator()
let lifecycleController = new AbortController()
let componentDestroyed = false
let performanceFrameId: number | null = null
let longTaskObserver: PerformanceObserver | null = null
const asyncWatchStops: Array<() => void> = []
let documentVisible = typeof document === 'undefined' || !document.hidden
let presentationReady = false
let overviewReady = false
let initialCameraReady = false
let initialIntersectionReady = false
let initialEnvironmentReady = false

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
const BUILDING_CACHE_BYTES = 128 * 1024 * 1024
const RECOVERY_BUILDING_CACHE_BYTES = 64 * 1024 * 1024
const LANDCOVER_CACHE_LIMIT = 4
const buildingZOffsetMeters = Number(import.meta.env.VITE_XIONGAN_BUILDING_Z_OFFSET_METERS ?? 0)
const enableBuildingContactShadows = import.meta.env.VITE_XIONGAN_BUILDING_CONTACT_SHADOWS === 'true'
const ACTIVE_FRAME_TIME_MS = 1_000 / MAP3D_NORMAL_FRAME_RATE
const STABLE_FRAME_TIME_MS = 1_000 / MAP3D_STABLE_FRAME_RATE
const projectScratch = new Vector3()
let activeDebugManifest: RealisticIntersectionManifest | null = null

function installSceneDebugApi(): void {
  if (!import.meta.env.DEV) return
  const debugWindow = window as Window & {
    __CITYPULSE_SCENE_DEBUG__?: {
      focusActive: (range?: number, heading?: number, pitch?: number) => boolean
      focusLocal: (x: number, y: number, range?: number, heading?: number, pitch?: number) => boolean
      edgeSamples: (spacingMeters?: number) => Array<{
        edgeId: string
        offsetMeters: number
        x: number
        y: number
        visible: boolean
      }>
    }
  }
  debugWindow.__CITYPULSE_SCENE_DEBUG__ = {
    focusActive: (range = 620, heading = 40, pitch = 68) => {
      if (!engine || !activeDebugManifest) return false
      const target = coordinateProjector([
        activeDebugManifest.origin.longitude,
        activeDebugManifest.origin.latitude,
        0,
      ])
      engine.map.flyTo(target, {
        range,
        heading,
        pitch,
        duration: 0,
        complete: () => undefined,
      })
      engine.requestRender()
      return true
    },
    focusLocal: (x, y, range = 620, heading = 40, pitch = 68) => {
      if (!engine || !activeDebugManifest?.origin.webMercator) return false
      const target = unprojectWebMercatorToBd09([
        activeDebugManifest.origin.webMercator[0] + x,
        activeDebugManifest.origin.webMercator[1] + y,
      ])
      engine.map.flyTo([target[0], target[1], 0], {
        range,
        heading,
        pitch,
        duration: 0,
        complete: () => undefined,
      })
      engine.requestRender()
      return true
    },
    edgeSamples: (spacingMeters = 10) => {
      if (!engine || !containerRef.value || !activeDebugManifest) return []
      const camera = (engine as unknown as { camera?: import('three').Camera }).camera
      if (!camera) return []
      const origin = coordinateProjector([
        activeDebugManifest.origin.longitude,
        activeDebugManifest.origin.latitude,
        0,
      ])
      const originScene = engine.map.projectArrayCoordinate([
        origin[0],
        origin[1],
        origin[2] ?? 0,
      ])
      const scale = activeDebugManifest.horizontalScale ?? 1
      const width = containerRef.value.clientWidth
      const height = containerRef.value.clientHeight
      return activeDebugManifest.edges.flatMap((edge) => {
        const points = edge.centerline ?? edge.lanes[0]?.renderPoints ?? edge.lanes[0]?.points ?? []
        const result: Array<{ edgeId: string; offsetMeters: number; x: number; y: number; visible: boolean }> = []
        let traversed = 0
        for (let index = 1; index < points.length; index += 1) {
          const start = points[index - 1]
          const end = points[index]
          const segmentLength = Math.hypot(end[0] - start[0], end[1] - start[1])
          const steps = Math.max(1, Math.ceil(segmentLength / Math.max(1, spacingMeters * scale)))
          for (let step = 0; step < steps; step += 1) {
            const ratio = step / steps
            const localX = start[0] + (end[0] - start[0]) * ratio
            const localY = start[1] + (end[1] - start[1]) * ratio
            projectScratch.set(
              originScene[0] + localX,
              originScene[1] + localY,
              (originScene[2] ?? 0) + REALISTIC_INTERSECTION_SURFACE_Z + 0.2,
            ).project(camera)
            result.push({
              edgeId: edge.id,
              offsetMeters: (traversed + segmentLength * ratio) / scale,
              x: (projectScratch.x * 0.5 + 0.5) * width,
              y: (-projectScratch.y * 0.5 + 0.5) * height,
              visible: projectScratch.z >= -1 && projectScratch.z <= 1,
            })
          }
          traversed += segmentLength
        }
        return result
      })
    },
  }
}

function projectDetectedEventToOverlay(
  longitude: number,
  latitude: number,
): { x: number; y: number } | null {
  if (!engine || !containerRef.value) return null
  const scene = coordinateProjector([
    longitude,
    latitude,
    REALISTIC_INTERSECTION_SURFACE_Z + 18,
  ])
  const camera = (engine as unknown as { camera?: import('three').Camera }).camera
  if (!camera) return null
  projectScratch.set(scene[0], scene[1], scene[2] ?? 0).project(camera)
  if (!Number.isFinite(projectScratch.x) || !Number.isFinite(projectScratch.y)) return null
  if (projectScratch.z < -1 || projectScratch.z > 1) return null
  const width = containerRef.value.clientWidth
  const height = containerRef.value.clientHeight
  return {
    x: (projectScratch.x * 0.5 + 0.5) * width,
    y: (-projectScratch.y * 0.5 + 0.5) * height,
  }
}

function syncTopologyCongestion(): void {
  if (!intersectionTopologyLayer) return
  const current = snapshot.value
  const routeIds = intersectionTopologyLayer.getRouteIds()
  if (!current || !detectedOverlayActive.value) {
    intersectionTopologyLayer.setRouteCongestion({})
    return
  }
  intersectionTopologyLayer.setRouteCongestion(
    buildDirectedRouteCongestionLevels(current.traffic_style, routeIds),
  )
}

function createBaiduProvider(): mapvthree.BaiduVectorTileProvider {
  baiduProvider = new mapvthree.BaiduVectorTileProvider({
    ak: baiduAk,
    styleJson: BAIDU_DARK_BASE_STYLE,
    displayOptions: createBaiduBaseDisplayOptions(showBaiduRoads),
    placeholderColor: '#122e2b',
  })
  return baiduProvider
}

function enableCameraInteraction(): void {
  if (!engine) return
  engine.controller.enabled = presentationReady && !cameraFlightActive
  engine.controller.enableRotate = true
  engine.controller.enableZoom = true
  engine.controller.enablePan = true
  engine.controller.enableTilt = true
  buildingTileset?.releaseCameraViewport()
  roadTileset?.releaseCameraViewport()
}

function beginBuildingCameraRevision(): void {
  buildingCameraRevision += 1
  buildingLoadTracker = createBuildingLoadTracker(performance.now(), buildingCameraRevision)
  buildingTilesReady = false
  if (presentationReady && buildingTileset) {
    tilesStatus.value = 'refining'
    tilesMessage.value = '建筑细化中 · 正在适配当前视野'
  }
}

function refreshIntersectionRoadLod(force = false): void {
  if (!props.active || !realisticIntersectionLayer) return
  const now = performance.now()
  if (!force && now - lastRoadLodRefreshAt < 100) return
  lastRoadLodRefreshAt = now
  realisticIntersectionLayer.refreshViewport()
  intersectionTopologyLayer?.refreshViewport()
  syncAnimationLoop()
}

function syncRuntimeDisturbanceRoads(): void {
  const events: RealisticRuntimeDisturbance[] = runtimeDisturbances.value.map((event) => ({
    eventId: event.eventId,
    intersectionId: event.intersectionId,
    eventType: event.eventType,
    state: event.state,
    laneIds: runtimeDisturbanceLaneIds(event),
    positionRatio: Number.isFinite(Number(event.details.position_ratio))
      ? Number(event.details.position_ratio)
      : undefined,
  }))
  realisticIntersectionLayer?.updateRuntimeDisturbances(events)
}

function cancelScheduledRoadLodRefresh(): void {
  if (roadLodIdleHandle == null) return
  const idleWindow = window as Window & {
    cancelIdleCallback?: (handle: number) => void
  }
  if (roadLodIdleUsesTimeout) window.clearTimeout(roadLodIdleHandle)
  else idleWindow.cancelIdleCallback?.(roadLodIdleHandle)
  roadLodIdleHandle = null
  roadLodIdleUsesTimeout = false
}

function scheduleRoadLodRefresh(): void {
  cancelScheduledRoadLodRefresh()
  const idleWindow = window as Window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number
  }
  const refresh = () => {
    roadLodIdleHandle = null
    roadLodIdleUsesTimeout = false
    if (componentDestroyed || interacting.value || !props.active) return
    refreshIntersectionRoadLod(true)
  }
  if (idleWindow.requestIdleCallback) {
    roadLodIdleHandle = idleWindow.requestIdleCallback(refresh, { timeout: 600 })
  } else {
    roadLodIdleUsesTimeout = true
    roadLodIdleHandle = window.setTimeout(refresh, 32)
  }
}

function markInteracting(): void {
  if (cameraFlightActive) return
  if (!interacting.value) {
    cancelScheduledRoadLodRefresh()
    beginBuildingCameraRevision()
    buildingTileset && (buildingTileset.errorTarget = buildingMovingErrorTarget())
    realisticIntersectionLayer?.setInteractionActive(true)
    vegetationRenderer?.setInteractionActive(true)
    roadsideFacilityRenderer?.refreshViewport()
  }
  interacting.value = true
  enableCameraInteraction()
  syncPerformanceSampling()
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  interactionEndTimer = setTimeout(() => {
    interacting.value = false
    realisticIntersectionLayer?.setInteractionActive(false)
    if (buildingTileset) buildingTileset.errorTarget = buildingIdleErrorTarget()
    vegetationRenderer?.setInteractionActive(false)
    roadsideFacilityRenderer?.refreshViewport()
    refreshIntersectionRoadLod(true)
    const stats = vehicleRenderer?.refreshViewport()
    if (stats) updateVehicleRenderStats(stats)
    engine?.requestRender()
    syncPerformanceSampling()
    interactionEndTimer = null
  }, 300)
}

function updateVehicleRenderStats(stats: VehicleRenderStats): void {
  vehicleStats.value = stats
  recordVehicleRuntimeDiagnostics(stats)
  const current = snapshot.value
  if (showRenderDiagnostics) {
    const diagnosticsWindow = window as Window & {
      __CITYPULSE_VEHICLE_DIAGNOSTICS__?: Record<string, unknown>
      __CITYPULSE_VEHICLE_POSE_DIAGNOSTICS__?: Record<string, unknown>
    }
    diagnosticsWindow.__CITYPULSE_VEHICLE_DIAGNOSTICS__ = {
      ...stats,
      activeVehicles: current?.metrics.active_vehicles ?? 0,
      requestedPlaybackSpeed: current?.playback_speed ?? 1,
      simulationProgressRate: stats.sourceRate,
      snapshotSequence: current?.sequence ?? -1,
      capturedAt: new Date().toISOString(),
    }
    diagnosticsWindow.__CITYPULSE_VEHICLE_POSE_DIAGNOSTICS__ = {
      sessionId: current?.session_id ?? '',
      intersectionId: activeIntersectionId.value,
      snapshotSequence: current?.sequence ?? -1,
      poseViolationCount: stats.poseViolationCount,
      vehicles: stats.vehiclePoseDiagnostics,
      capturedAt: new Date().toISOString(),
    }
  }
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
  if (showRenderDiagnostics && stats.underrunCount > lastVehicleUnderrunCount) {
    lastVehicleUnderrunCount = stats.underrunCount
    console.debug('[vehicle-render] motion buffer underrun', {
      sourceGapP95Ms: Math.round(stats.sourceGapP95Ms),
      sourceGapP99Ms: Math.round(stats.sourceGapP99Ms),
      bufferSeconds: Number(stats.bufferSeconds.toFixed(2)),
      sourceRate: Number(stats.sourceRate.toFixed(2)),
      fps: stats.fps,
      quality: stats.quality,
      visibleVehicles: stats.visibleCount,
      vehicleLimit: stats.vehicleLimit,
    })
  }
}

function buildingIdleErrorTarget(): number {
  return stableRenderMode.value ? 16 : BUILDING_IDLE_ERROR_TARGET
}

function buildingMovingErrorTarget(): number {
  return stableRenderMode.value ? 36 : BUILDING_MOVING_ERROR_TARGET
}

function applyStableRenderingBudget(reason: string): void {
  performanceGovernor.forceStableMode()
  if (!stableRenderMode.value) stableRenderMode.value = true
  vehicleRenderer?.setStableMode(true)
  renderQuality = 'reduced'
  if (engine) {
    const pixelRatioRendering = engine.rendering as typeof engine.rendering & { pixelRatio: number }
    pixelRatioRendering.pixelRatio = 1
    engine.requestRender()
    syncAnimationLoop()
  }
  if (buildingTileset) {
    buildingTileset.errorTarget = interacting.value
      ? buildingMovingErrorTarget()
      : buildingIdleErrorTarget()
    const cacheControlled = buildingTileset as mapvthree.Default3DTiles & { cacheBytes?: number }
    cacheControlled.cacheBytes = RECOVERY_BUILDING_CACHE_BYTES
  }
  if (showRenderDiagnostics) {
    const diagnosticsWindow = window as Window & {
      __CITYPULSE_MAP3D_PERFORMANCE__?: Record<string, unknown>
    }
    diagnosticsWindow.__CITYPULSE_MAP3D_PERFORMANCE__ = {
      ...performanceGovernor.stats(),
      reason,
      capturedAt: new Date().toISOString(),
    }
  }
}

function performanceSamplingActive(): boolean {
  const state = snapshot.value?.state
  const simulationActive = state === 'STARTING' || state === 'RUNNING' || state === 'STOPPING'
  return props.active && documentVisible && (interacting.value || simulationActive)
}

function syncPerformanceSampling(): void {
  if (!performanceSamplingActive()) {
    if (performanceFrameId != null) cancelAnimationFrame(performanceFrameId)
    performanceFrameId = null
    return
  }
  if (performanceFrameId != null) return
  const sample = (nowMs: number) => {
    performanceFrameId = null
    if (performanceGovernor.recordFrame(nowMs)) applyStableRenderingBudget('sustained_low_fps')
    if (performanceSamplingActive()) performanceFrameId = requestAnimationFrame(sample)
  }
  performanceFrameId = requestAnimationFrame(sample)
}

function startLongTaskMonitoring(): void {
  if (longTaskObserver || typeof PerformanceObserver === 'undefined') return
  try {
    longTaskObserver = new PerformanceObserver((list) => {
      if (!props.active) return
      for (const entry of list.getEntries()) {
        if (entry.duration <= 100) continue
        recordSimulationLongTasks(1)
        if (performanceGovernor.recordLongTask(entry.duration, performance.now())) {
          applyStableRenderingBudget('repeated_long_tasks')
        }
      }
    })
    longTaskObserver.observe({ entryTypes: ['longtask'] })
  } catch {
    longTaskObserver = null
  }
}

function syncAnimationLoop(): void {
  if (!engine) return
  const state = snapshot.value?.state
  const simulationActive = state === 'STARTING' || state === 'RUNNING' || state === 'STOPPING'
  const topologyActive = simulationActive && Boolean(intersectionTopologyLayer?.animationActive)
  const active = props.active && documentVisible && (simulationActive || topologyActive)
  engine.rendering.animationLoopFrameTime = stableRenderMode.value
    ? STABLE_FRAME_TIME_MS
    : ACTIVE_FRAME_TIME_MS
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
  if (!props.active) return
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
  if (buildingTileset) {
    const statistics = buildingTileset.statistics as typeof buildingTileset.statistics & {
      numberOfAttemptedRequests?: number
    }
    buildingLoadTracker = advanceBuildingLoadTracker(buildingLoadTracker, {
      readyTiles: statistics.numberOfTilesWithContentReady,
      pendingRequests: statistics.numberOfPendingRequests,
      processingTiles: statistics.numberOfTilesProcessing,
      attemptedRequests: statistics.numberOfAttemptedRequests ?? 0,
      totalTiles: statistics.numberOfTilesTotal,
      cameraRevision: buildingCameraRevision,
      nowMs: performance.now(),
    }, renderQuality)
  }
  const nextBuildingTilesReady = buildingPresentationUsable(buildingLoadTracker)
  if (nextBuildingTilesReady !== buildingTilesReady) {
    buildingTilesReady = nextBuildingTilesReady
    if (nextBuildingTilesReady && buildingTileset) configureBuildingShadows(buildingTileset)
    engine?.requestRender()
  }

  const roadSatisfied = realisticDetailReady || !enableStaticRoadTileset || roadTilesReady
  const buildingSatisfied = !enableLocalTileset
    || buildingTilesReady
    || presentationReady
    || showBaiduBuildings
  if (roadSatisfied && buildingSatisfied) {
    const settled = !enableLocalTileset || buildingPresentationSettled(buildingLoadTracker)
    tilesStatus.value = settled ? 'ready' : 'refining'
    if (settled) {
      tilesMessage.value = `3D Tiles 已就绪 · 可见 ${ready} · 已载 ${loaded}${total > 0 ? `/${total}` : ''}`
    } else {
      const coverage = Math.round(buildingLoadTracker.coverage * 100)
      tilesMessage.value = `建筑细化中 · 已准备 ${buildingLoadTracker.readyTiles} · 覆盖 ${coverage}% · 活动请求 ${buildingLoadTracker.activeRequests}`
    }
    return
  }
  tilesStatus.value = 'loading'
  tilesMessage.value = `3D Tiles 加载中 · 请求 ${pending}`
  if (pending > 0) engine?.requestRender()
}

function stopTilesStatusTimer(): void {
  if (tilesStatusTimer) clearInterval(tilesStatusTimer)
  tilesStatusTimer = null
}

function startTilesStatusTimer(): void {
  if (!props.active || tilesStatusTimer || (!enableLocalTileset && !roadTileset)) return
  tilesStatusTimer = setInterval(updateTilesStatus, 500)
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

interface PreparedIntersectionEnvironment {
  environment: IntersectionEnvironmentManifest
  facilities: ReturnType<typeof parseSceneFacilityManifest> | null
}

async function switchRealisticIntersection(
  intersectionId: string,
  trackInitialPresentation = false,
  focusCamera = true,
): Promise<boolean> {
  if (!realisticIntersectionLayer || !intersectionId) return false
  const transaction = sceneSwitchCoordinator.begin(intersectionId)
  const signal = transaction.signal
  const revision = ++sceneSwitchRevision
  let prepared = false
  let committed = false
  setSceneLoading()
  if (trackInitialPresentation) emit('loading', '正在准备当前高精度路口')
  try {
    const manifest = await realisticIntersectionLayer.prepare(intersectionId, signal)
    prepared = true
    if (!sceneSwitchCoordinator.isCurrent(transaction) || revision !== sceneSwitchRevision || activeIntersectionId.value !== intersectionId) {
      realisticIntersectionLayer.discard(intersectionId)
      return false
    }
    vehicleRenderer?.beginViewportTransition()
    const cameraPromise = new Promise<boolean>((resolve) => {
      if (!focusCamera) {
        resolve(true)
        return
      }
      let settled = false
      const finish = (ready: boolean) => {
        if (settled) return
        settled = true
        signal.removeEventListener('abort', onAbort)
        resolve(ready)
      }
      const onAbort = () => finish(false)
      signal.addEventListener('abort', onAbort, { once: true })
      mapView.focusIntersection(
        [manifest.origin.longitude, manifest.origin.latitude],
        manifest.intersectionId,
        {
          force: true,
          duration: 900,
          complete: () => finish(true),
        },
      )
    })
    const [cameraReady, preparedEnvironment] = await Promise.all([
      cameraPromise,
      prepareIntersectionEnvironment(intersectionId, signal),
    ])
    if (
      !cameraReady
      || !sceneSwitchCoordinator.isCurrent(transaction)
      || revision !== sceneSwitchRevision
      || activeIntersectionId.value !== intersectionId
    ) {
      realisticIntersectionLayer.discard(intersectionId)
      return false
    }

    realisticIntersectionLayer.activate(intersectionId)
    activeDebugManifest = manifest
    installSceneDebugApi()
    syncRuntimeDisturbanceRoads()
    scheduleRoadLodRefresh()
    realisticDetailReady = true
    vehicleRenderer?.commitViewportTransition(
      manifest.intersectionId,
      createIntersectionLaneHeadingResolver(manifest),
      createIntersectionLanePoseResolver(manifest, coordinateProjector),
      fullyExcludedSurfaceEdgeIds(manifest),
      surfaceVisibilityIntervals(manifest),
    )
    syncRoadRendering(geojson.value)
    roadsideFacilityRenderer?.setRealisticDetailActive(true)
    realisticIntersectionLayer.updateSignals(trafficView.value?.intersections ?? null)
    commitIntersectionEnvironment(intersectionId, preparedEnvironment, revision)
    committed = true

    const current = snapshot.value
    const stats = vehicleRenderer?.update(trafficView.value?.vehicles ?? [], {
      sessionId: current?.session_id ?? '',
      state: current?.state ?? null,
      sequence: current?.sequence ?? -1,
      elapsedSeconds: current?.elapsed_seconds ?? 0,
      laneRuntimeById: current?.intersections?.[intersectionId]?.lanes ?? {},
    }, true)
    if (stats) updateVehicleRenderStats(stats)
    if (trackInitialPresentation) {
      initialCameraReady = true
      initialIntersectionReady = true
      initialEnvironmentReady = true
    }
    setSceneReady(intersectionId)
    return true
  } catch (cause) {
    if (prepared && !committed) realisticIntersectionLayer.discard(intersectionId)
    if (cause instanceof DOMException && cause.name === 'AbortError') return false
    realisticDetailReady = realisticIntersectionLayer.activeIntersectionId !== null
    syncRoadRendering(geojson.value)
    roadsideFacilityRenderer?.setRealisticDetailActive(realisticDetailReady)
    setSceneError(cause instanceof Error ? cause.message : '高精度路口加载失败')
    return false
  } finally {
    const current = sceneSwitchCoordinator.complete(transaction)
    if (current && !committed) vehicleRenderer?.cancelViewportTransition()
    syncPerformanceSampling()
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
  signal?: AbortSignal,
): Promise<void> {
  if (!engine || !enableShowcaseLayers) return
  if (showcaseGeoJsonLayers.has(intersectionId)) {
    showcaseGeoJsonUsedAt.set(intersectionId, performance.now())
    return
  }
  const existing = showcaseGeoJsonLoading.get(intersectionId)
  if (existing && !existing.signal?.aborted) return existing.promise
  if (existing) showcaseGeoJsonLoading.delete(intersectionId)
  const loadingPromise = (async () => {
    const manifest = environment ?? await loadIntersectionEnvironmentManifest(intersectionId)
    if (!manifest.geojson || !engine || showcaseGeoJsonLayers.has(intersectionId)) return
    const layers = new ShowcaseGeoJsonLayers(engine, coordinateProjector)
    try {
      await layers.load(manifest.geojson, signal)
      if (!engine || signal?.aborted) {
        layers.destroy()
        if (signal?.aborted) throw new DOMException('Landcover loading aborted', 'AbortError')
        return
      }
      layers.setVisible(false)
      showcaseGeoJsonLayers.set(intersectionId, layers)
      showcaseGeoJsonUsedAt.set(intersectionId, performance.now())
    } catch (cause) {
      layers.destroy()
      throw cause
    }
  })().finally(() => {
    if (showcaseGeoJsonLoading.get(intersectionId)?.promise === loadingPromise) {
      showcaseGeoJsonLoading.delete(intersectionId)
    }
  })
  showcaseGeoJsonLoading.set(intersectionId, { promise: loadingPromise, signal })
  return loadingPromise
}

function trimLandcoverCache(protectedIds: Set<string> = new Set()): void {
  const candidates = [...showcaseGeoJsonLayers.keys()]
    .filter((id) => id !== realisticIntersectionLayer?.activeIntersectionId && !protectedIds.has(id))
    .sort((left, right) => (
      (showcaseGeoJsonUsedAt.get(left) ?? 0) - (showcaseGeoJsonUsedAt.get(right) ?? 0)
    ))
  while (showcaseGeoJsonLayers.size > LANDCOVER_CACHE_LIMIT && candidates.length > 0) {
    const id = candidates.shift()!
    showcaseGeoJsonLayers.get(id)?.destroy()
    showcaseGeoJsonLayers.delete(id)
    showcaseGeoJsonUsedAt.delete(id)
  }
}

async function prepareIntersectionEnvironment(
  intersectionId: string,
  signal: AbortSignal,
): Promise<PreparedIntersectionEnvironment> {
  const environment = await loadIntersectionEnvironmentManifest(intersectionId)
  if (signal.aborted) throw new DOMException('Environment loading aborted', 'AbortError')
  const facilitiesPromise = environment.facilitiesUrl && enableRoadsideFacilities
    ? fetch(environment.facilitiesUrl, { signal }).then(async (response) => {
        if (!response.ok) throw new Error(`Roadside facilities returned HTTP ${response.status}`)
        return parseSceneFacilityManifest(await response.json())
      })
    : Promise.resolve(null)
  const landcoverPromise = ensureIntersectionLandcover(intersectionId, environment, signal)
    .catch((cause: unknown) => {
      if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
      console.warn(`[landcover] ${intersectionId} optional resources failed`, cause)
    })
  const streetlightPromise = environment.streetlight && roadsideFacilityRenderer
    ? roadsideFacilityRenderer.prepareStreetlight(
        environment.streetlight.modelUrl,
        environment.streetlight.heightMeters,
        environment.streetlight.modelYawDegrees ?? 0,
      )
    : Promise.resolve()
  const [facilities] = await Promise.all([
    facilitiesPromise,
    landcoverPromise,
    streetlightPromise,
  ])
  if (signal.aborted) throw new DOMException('Environment loading aborted', 'AbortError')
  return { environment, facilities }
}

function commitIntersectionEnvironment(
  intersectionId: string,
  prepared: PreparedIntersectionEnvironment,
  revision: number,
): void {
  if (revision !== sceneSwitchRevision || activeIntersectionId.value !== intersectionId) return
  for (const [id, layers] of showcaseGeoJsonLayers) layers.setVisible(id === intersectionId)
  showcaseGeoJsonUsedAt.set(intersectionId, performance.now())
  trimLandcoverCache(new Set([intersectionId]))
  if (prepared.facilities && roadsideFacilityRenderer) {
    roadsideFacilityRenderer.render(prepared.facilities)
    roadsideFacilityRenderer.updateSignals(trafficView.value?.intersections ?? null)
  } else {
    roadsideFacilityRenderer?.clearScene()
  }
  if (prepared.environment.vegetation && enableVegetation && vegetationRenderer) {
    void vegetationRenderer.load(
      prepared.environment.vegetation.manifestUrl,
      prepared.environment.vegetation.modelUrl,
    ).catch((cause: unknown) => console.warn(`[vegetation] ${intersectionId} optional resources failed`, cause))
  } else {
    vegetationRenderer?.clearScene()
  }
  if (showcaseModelLayers) {
    void showcaseModelLayers.loadLandmark(prepared.environment.detailModel ?? null)
      .catch((cause: unknown) => console.warn(`[detail-model] ${intersectionId} optional resource failed`, cause))
  }
}

function createBuildingTileset(url: string): mapvthree.Default3DTiles {
  if (!engine) throw new Error('3D map engine is unavailable')
  const tileset = engine.add(new mapvthree.Default3DTiles({
    url,
    errorTarget: interacting.value
      ? buildingMovingErrorTarget()
      : buildingIdleErrorTarget(),
    forceUnlit: false,
    dynamicScreenSpaceError: false,
    foveatedScreenSpaceError: false,
    progressiveResolutionHeightFraction: 0.3,
      // Keep the first batch alive while the overview camera and providers settle.
      // Request culling is restored as soon as the black presentation gate opens.
      cullRequestsWhileMoving: false,
    cullRequestsWhileMovingMultiplier: 60,
    cacheBytes: stableRenderMode.value ? RECOVERY_BUILDING_CACHE_BYTES : BUILDING_CACHE_BYTES,
  })) as mapvthree.Default3DTiles
  if (Number.isFinite(buildingZOffsetMeters)) tileset.position.z = buildingZOffsetMeters
  tileset.releaseCameraViewport()
  return tileset
}

async function validateGlobalBuildingSource(): Promise<void> {
  if (!enableLocalTileset) return
  const tilesetResponse = await fetch(tilesetUrl)
  if (!tilesetResponse.ok) {
    throw new Error(`全域建筑 tileset 加载失败 (${tilesetResponse.status})`)
  }
  const tilesetJson = await tilesetResponse.json() as { asset?: unknown; root?: unknown }
  if (!tilesetJson.asset || !tilesetJson.root) {
    throw new Error('全域建筑 tileset 解析失败：缺少 asset 或 root')
  }
  if (!import.meta.env.DEV) return
  emit('loading', '正在检查全域本地建筑数据')
  const manifestUrl = buildingTilesetManifestUrl(tilesetUrl, window.location.href)
  const response = await fetch(manifestUrl)
  if (!response.ok) {
    throw new Error(`全域建筑 manifest 加载失败 (${response.status})`)
  }
  const diagnosis = assertGlobalBuildingSource(await response.json())
  console.info('[building-tileset] global source verified', {
    url: tilesetUrl,
    sourceTiles: diagnosis.sourceTiles,
    outputTiles: diagnosis.outputTiles,
    vertexCount: diagnosis.vertexCount,
  })
}

function addGlobalBuildingTileset(): void {
  if (!engine || !enableLocalTileset || buildingTileset) return
  tilesStatus.value = 'loading'
  buildingLoadTracker = createBuildingLoadTracker(performance.now(), buildingCameraRevision)
  buildingTilesReady = false
  buildingTileset = createBuildingTileset(tilesetUrl)
  engine.requestRender()
}

function presentationSignals(): Map3dPresentationSignals {
  const provider = baiduProvider as unknown as { isReady?: () => boolean } | null
  return {
    providerReady: provider ? provider.isReady?.() ?? true : false,
    cameraReady: initialCameraReady,
    overviewReady,
    intersectionReady: initialIntersectionReady,
    environmentReady: initialEnvironmentReady,
    buildingRequired: enableLocalTileset,
    buildingUsable: buildingTilesReady,
    buildingReadyTiles: buildingLoadTracker.readyTiles,
    buildingCoverage: buildingLoadTracker.coverage,
  }
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Operation aborted', 'AbortError'))
      return
    }
    const timer = window.setTimeout(finish, milliseconds)
    function finish() {
      signal.removeEventListener('abort', abort)
      resolve()
    }
    function abort() {
      window.clearTimeout(timer)
      reject(new DOMException('Operation aborted', 'AbortError'))
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}

async function waitForPresentationGate(
  signal: AbortSignal = lifecycleController.signal,
): Promise<boolean> {
  while (engine && !signal.aborted) {
    updateTilesStatus()
    const nowMs = performance.now()
    const elapsedMs = nowMs - presentationStartedAt
    const signals = presentationSignals()
    emit('loading', map3dLoadingStage(signals))
    const decision = resolveMap3dPresentationDecision(
      signals,
      buildingLoadTracker,
      elapsedMs,
      nowMs,
    )
    if (decision === 'present') {
      if (buildingTilesReady || !signals.buildingRequired) return true
      console.info('[map3d-load] opening usable scene while buildings continue refining', {
        elapsedMs: Math.round(elapsedMs),
        readyTiles: buildingLoadTracker.readyTiles,
        demandedTiles: buildingLoadTracker.demandedTiles,
        coverage: buildingLoadTracker.coverage,
        activeRequests: buildingLoadTracker.activeRequests,
      })
      return true
    }
    if (decision === 'stalled') {
      throw new Error(
        `3D Tiles 加载连续 ${Math.round((nowMs - buildingLoadTracker.lastProgressAtMs) / 1000)} 秒无进展`
        + `（已准备 ${buildingLoadTracker.readyTiles}，活动请求 ${buildingLoadTracker.activeRequests}）`,
      )
    }
    if (decision === 'hard-timeout') {
      throw new Error(
        `3D 场景加载超过 ${MAP3D_PRESENTATION_HARD_TIMEOUT_MS / 1000} 秒：${map3dLoadingStage(signals)}`,
      )
    }
    engine.requestRender()
    await abortableDelay(BUILDING_STABLE_SAMPLE_INTERVAL_MS, signal)
  }
  return false
}

async function waitForFinalRenderFrames(
  frameCount = FINAL_RENDER_FRAME_COUNT,
  signal: AbortSignal = lifecycleController.signal,
  timeoutMs = 5_000,
): Promise<boolean> {
  const activeEngine = engine
  if (!activeEngine) return false
  const renderLifecycle = activeEngine as unknown as {
    addBeforeRenderListener: (callback: () => void) => void
    removeBeforeRenderListener: (callback: () => void) => void
  }
  return new Promise((resolve) => {
    let renderedFrames = 0
    let settled = false
    const finish = (ready: boolean) => {
      if (settled) return
      settled = true
      window.clearInterval(requestTimer)
      window.clearTimeout(timeoutTimer)
      signal.removeEventListener('abort', onAbort)
      renderLifecycle.removeBeforeRenderListener(onBeforeRender)
      resolve(ready)
    }
    const onAbort = () => finish(false)
    const onBeforeRender = () => {
      if (engine !== activeEngine) {
        finish(false)
        return
      }
      renderedFrames += 1
      if (renderedFrames >= frameCount) finish(true)
    }
    const requestTimer = window.setInterval(() => {
      if (engine !== activeEngine) {
        finish(false)
        return
      }
      activeEngine.requestRender()
    }, 16)
    const timeoutTimer = window.setTimeout(() => finish(false), timeoutMs)
    signal.addEventListener('abort', onAbort, { once: true })
    renderLifecycle.addBeforeRenderListener(onBeforeRender)
    activeEngine.requestRender()
  })
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

function syncEngineResolution(): void {
  engineResizeFrameId = null
  const activeEngine = engine
  const container = containerRef.value
  if (!activeEngine || !container || componentDestroyed) return
  const width = Math.max(1, Math.floor(container.clientWidth))
  const height = Math.max(1, Math.floor(container.clientHeight))
  const rendering = activeEngine.rendering as unknown as { resolution: Vector2 }
  const resolution = rendering.resolution
  if (resolution.x === width && resolution.y === height) return
  rendering.resolution = new Vector2(width, height)
  if (topologyNodes.value.length > 0) applyGlobalNavigationBounds(topologyNodes.value)
  overlayViewToken.value += 1
  activeEngine.requestRender()
}

function scheduleEngineResize(): void {
  if (engineResizeFrameId != null || componentDestroyed) return
  engineResizeFrameId = requestAnimationFrame(syncEngineResolution)
}

function observeEngineResize(container: HTMLElement): void {
  engineResizeObserver?.disconnect()
  engineResizeObserver = new ResizeObserver(scheduleEngineResize)
  engineResizeObserver.observe(container)
  scheduleEngineResize()
}

function stopEngineResizeObserver(): void {
  engineResizeObserver?.disconnect()
  engineResizeObserver = null
  if (engineResizeFrameId != null) cancelAnimationFrame(engineResizeFrameId)
  engineResizeFrameId = null
}

function registerThreeMapController(): void {
  mapView.registerThreeMap({
    flyTo: (target, options) => {
      if (options.force || !interacting.value) {
        beginBuildingCameraRevision()
        const revision = ++cameraFlightRevision
        cameraFlightGuard?.cancel()
        cameraFlightActive = options.duration > 0
        if (cameraFlightActive && engine) engine.controller.enabled = false
        const placedTarget = placeBaiduCameraTarget(target, scenePlacement)
        const finishFlight = () => {
          if (revision !== cameraFlightRevision) return
          cameraFlightGuard = null
          cameraFlightActive = false
          enableCameraInteraction()
          refreshIntersectionRoadLod(true)
          options.complete()
        }
        cameraFlightGuard = createCameraFlightGuard({
          timeoutMs: cameraFlightWatchdogDelay(options.duration),
          onTimeout: () => {
            if (revision !== cameraFlightRevision || !engine) return
            engine.map.flyTo(placedTarget, {
              ...options,
              duration: 0,
              complete: () => undefined,
            })
          },
          onComplete: finishFlight,
        })
        engine?.map.flyTo(placedTarget, {
          ...options,
          complete: cameraFlightGuard.complete,
        })
      }
    },
    setViewport: (points, options) => {
      if (options.force || !interacting.value) {
        beginBuildingCameraRevision()
        engine?.map.setViewport(
          points.map((point) => placeBaiduCameraTarget(point, scenePlacement)),
          options,
        )
        refreshIntersectionRoadLod(true)
      }
    },
    setRangeLimits: (minimum, maximum) => {
      engine?.map.setMinRange(minimum)
      engine?.map.setMaxRange(maximum)
    },
  })
}

function handleWebglContextLost(event: Event): void {
  event.preventDefault()
  if (componentDestroyed) return
  error.value = '三维图形上下文已丢失，正在重建场景'
  loading.value = true
  presentationReady = false
  sceneSwitchCoordinator.cancel()
  sceneSwitchRevision += 1
  lifecycleController.abort()
  vehicleRenderer?.setActive(false)
  if (engine) engine.rendering.enableAnimationLoop = false
  const failure = new Error('WebGL 上下文丢失，需要重建三维场景')
  failure.name = 'WebGLContextLostError'
  window.setTimeout(() => {
    if (!componentDestroyed) emit('fatal', failure)
  }, 0)
}

async function initMap(): Promise<void> {
  const container = containerRef.value
  if (!container || componentDestroyed) return
  presentationStartedAt = performance.now()
  const capability = detectMap3dCapability()
  if (!capability.supported) {
    throw new Error(capability.reason ?? '当前浏览器不支持三维地图')
  }
  if (!baiduAk) {
    throw new Error('未配置 VITE_BAIDU_MAP_AK，请先填写百度地图浏览器端 AK')
  }
  renderQuality = capability.quality === 'reduced' ? 'reduced' : 'full'
  if (renderQuality === 'reduced') {
    stableRenderMode.value = true
    performanceGovernor.forceStableMode()
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
      pixelRatio: Math.min(window.devicePixelRatio, stableRenderMode.value ? 1 : 1.25),
    },
  })
  engine.map.setMinRange(BAIDU_3D_MIN_RANGE)
  engine.map.setMaxRange(BAIDU_3D_MAX_RANGE)
  observeEngineResize(container)
  const qualityEngine = engine as unknown as {
    rendering: { features?: { bloom?: { enabled: boolean } } }
    clock?: { _setTimeLegacy?: (seconds: number) => void }
  }
  if (qualityEngine.rendering.features?.bloom) {
    qualityEngine.rendering.features.bloom.enabled = false
  }
  startLongTaskMonitoring()
  if (stableRenderMode.value) applyStableRenderingBudget(
    props.recoveryMode ? 'webgl_recovery' : 'reduced_device_capability',
  )
  qualityEngine.clock?._setTimeLegacy?.(14.5 * 3600)
  sky = engine.add(new mapvthree.DefaultSky())
  sky.color = new Color('#152535')
  sky.highColor = new Color('#07111d')
  sky.skyLightIntensity = 1.3
  ;(sky as unknown as { sunIntensityScale: number }).sunIntensityScale = 0.65
  presentationReady = false
  overviewReady = false
  initialCameraReady = false
  initialIntersectionReady = false
  initialEnvironmentReady = false
  engine.controller.enabled = false
  emit('loading', '正在初始化百度三维底图')
  enableCameraInteraction()
  bindContainerInteraction(container)
  container.addEventListener('webglcontextlost', handleWebglContextLost, true)

  emit('loading', '正在定位20路口总览视角')
  mapView.setCameraPreset('overview')
  registerThreeMapController()
  if (!await waitForFinalRenderFrames()) {
    throw new Error('20路口总览视角定位失败')
  }
  initialCameraReady = true

  if (enableLocalTileset) {
    await validateGlobalBuildingSource()
    tilesMessage.value = '全域建筑源已验证，等待总览资源完成…'
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
  if (componentDestroyed || lifecycleController.signal.aborted || !engine) return
  roadRenderer = roadRendererMode === 'basic'
    ? new BaiduRoadNetworkRenderer(engine, coordinateProjector)
    : new BaiduDetailedRoadRenderer(engine, coordinateProjector)
  vehicleRenderer = new BaiduVehicleRenderer(engine, coordinateProjector)
  vehicleRenderer.setStableMode(stableRenderMode.value)
  realisticIntersectionLayer = new MapvRealisticIntersectionLayer(engine, coordinateProjector)
  if (enableIntersectionTopology) {
    intersectionTopologyLayer = new IntersectionTopologyLayer(engine, coordinateProjector)
  }
  if (enableShowcaseLayers) {
    showcaseModelLayers = new ShowcaseModelLayers(engine, coordinateProjector)
  }
  if (enableRoadsideFacilities) {
    roadsideFacilityRenderer = new RoadsideFacilityRenderer(
      engine,
      coordinateProjector,
    )
  }
  if (enableVegetation) {
    vegetationRenderer = new VegetationRenderer(engine, coordinateProjector)
    vegetationRenderer.setInteractionActive(interacting.value)
  }
  asyncWatchStops.push(watch(
    trafficView,
    (value) => {
      const current = snapshot.value
      const stats = vehicleRenderer?.update(value?.vehicles ?? [], {
        sessionId: current?.session_id ?? '',
        state: current?.state ?? null,
        sequence: current?.sequence ?? -1,
        elapsedSeconds: current?.elapsed_seconds ?? 0,
        laneRuntimeById: current?.intersections?.[activeIntersectionId.value]?.lanes ?? {},
      })
      if (stats) updateVehicleRenderStats(stats)
      roadsideFacilityRenderer?.updateSignals(value?.intersections ?? null)
      realisticIntersectionLayer?.updateSignals(value?.intersections ?? null)
    },
    { immediate: true },
  ))
  asyncWatchStops.push(watch(
    renderSessionRevision,
    () => {
      vehicleRenderer?.clear()
      engine?.requestRender()
    },
    { flush: 'sync' },
  ))
  asyncWatchStops.push(watch(snapshot, syncAnimationLoop, { immediate: true }))
  asyncWatchStops.push(watch(runtimeDisturbances, syncRuntimeDisturbanceRoads, {
    deep: true,
    immediate: true,
  }))
  asyncWatchStops.push(watch(
    snapshot,
    () => {
      syncTopologyCongestion()
    },
    { immediate: true },
  ))
  asyncWatchStops.push(watch(
    () => props.active,
    (active) => {
      vehicleRenderer?.setActive(active)
      if (active) {
        refreshIntersectionRoadLod(true)
        updateTilesStatus()
        if (presentationReady) startTilesStatusTimer()
        engine?.requestRender()
      } else {
        stopTilesStatusTimer()
      }
      syncAnimationLoop()
    },
    { immediate: true },
  ))
  asyncWatchStops.push(watch(
    geojson,
    syncMapRendering,
    { immediate: true },
  ))

  if (enableIntersectionTopology && intersectionTopologyLayer) {
    emit('loading', '正在加载 20 路口道路总览')
    await loadEdgeTopologySegmentMap().catch((cause: unknown) => {
      console.warn('[edge-topology] map unavailable', cause)
    })
    const nodes = await intersectionTopologyLayer.load()
    vehicleRenderer?.setHeadingAnchors(nodes)
    topologyNodes.value = nodes
    applyGlobalNavigationBounds(nodes)
    syncTopologyCongestion()
    const intersectionIds = nodes.map((node) => node.intersectionId)
    await realisticIntersectionLayer.prepareOverview(intersectionIds)
    if (componentDestroyed || lifecycleController.signal.aborted) return
    refreshIntersectionRoadLod(true)
    intersectionTopologyLayer.setActiveIntersection(activeIntersectionId.value)
    syncAnimationLoop()
  }
  overviewReady = true

  const initialSceneReady = await switchRealisticIntersection(
    activeIntersectionId.value,
    true,
    false,
  )
  if (!initialSceneReady) throw new Error(`${activeIntersectionId.value} 高精度路口加载失败`)

  addGlobalBuildingTileset()

  asyncWatchStops.push(watch(
    [activeIntersectionId, selectionRevision],
    ([intersectionId]) => {
      intersectionTopologyLayer?.setActiveIntersection(intersectionId)
      void switchRealisticIntersection(intersectionId)
    },
  ))

  if (!await waitForPresentationGate()) return
  emit('loading', '正在完成三维场景渲染')
  if (!await waitForFinalRenderFrames()) return
  presentationReady = true
  if (buildingTileset) buildingTileset.cullRequestsWhileMoving = true
  enableCameraInteraction()
  updateTilesStatus()
  if (enableLocalTileset || roadTileset) {
    startTilesStatusTimer()
  }
  engine.requestRender()
  loading.value = false
  emit('ready')
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
  componentDestroyed = true
  activeDebugManifest = null
  if (import.meta.env.DEV) {
    delete (window as Window & { __CITYPULSE_SCENE_DEBUG__?: unknown }).__CITYPULSE_SCENE_DEBUG__
    delete (window as Window & {
      __CITYPULSE_VEHICLE_POSE_DIAGNOSTICS__?: unknown
    }).__CITYPULSE_VEHICLE_POSE_DIAGNOSTICS__
  }
  lifecycleController.abort()
  sceneSwitchCoordinator.cancel()
  sceneSwitchRevision += 1
  while (asyncWatchStops.length > 0) asyncWatchStops.pop()?.()
  document.removeEventListener('visibilitychange', handleDocumentVisibility)
  cameraFlightGuard?.cancel()
  cameraFlightGuard = null
  cameraFlightRevision += 1
  cameraFlightActive = false
  mapView.unregisterThreeMap()
  unbindContainerInteraction(containerRef.value)
  containerRef.value?.removeEventListener('webglcontextlost', handleWebglContextLost, true)
  stopEngineResizeObserver()
  stopTilesStatusTimer()
  if (interactionEndTimer) clearTimeout(interactionEndTimer)
  interactionEndTimer = null
  cancelScheduledRoadLodRefresh()
  if (performanceFrameId != null) cancelAnimationFrame(performanceFrameId)
  performanceFrameId = null
  longTaskObserver?.disconnect()
  longTaskObserver = null
  roadRenderer?.destroy()
  roadRenderer = null
  vehicleRenderer?.destroy()
  vehicleRenderer = null
  showcaseGeoJsonLayers.forEach((layers) => layers.destroy())
  showcaseGeoJsonLayers.clear()
  showcaseGeoJsonLoading.clear()
  showcaseGeoJsonUsedAt.clear()
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
  topologyNodes.value = []
  realisticDetailReady = false
  if (sky && engine) engine.remove(sky)
  sky = null
  if (buildingTileset && engine) engine.remove(buildingTileset)
  if (roadTileset && engine) engine.remove(roadTileset)
  buildingTileset = null
  roadTileset = null
  roadTilesetManifest = null
  buildingTilesReady = false
  buildingLoadTracker = createBuildingLoadTracker()
  roadTilesReady = false
  engine?.dispose()
  engine = null
})
</script>

<template>
  <div class="app-baidu-three-map">
    <div ref="containerRef" class="app-baidu-three-map__canvas" />
    <DetectedEventOverlay
      :cards="detectedEventCards"
      :snapshot="snapshot"
      :project="projectDetectedEventToOverlay"
      :active="detectedOverlayActive && !loading && !error"
      :continuous="interacting"
      :view-token="overlayViewToken"
    />
    <RuntimeDisturbanceOverlay
      :events="runtimeDisturbanceMarkers"
      :unmapped-events="unmappedRuntimeEvents"
      :project="projectDetectedEventToOverlay"
      :active="props.active && !loading && !error"
      :continuous="interacting"
      :view-token="overlayViewToken"
    />
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
    <div v-if="vehicleBufferBusy" class="app-baidu-three-map__vehicle-status">
      数据快照延迟，画面正在等待最新数据
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

.app-baidu-three-map__vehicle-status {
  position: absolute;
  left: 50%;
  bottom: 62px;
  z-index: 2;
  padding: 5px 10px;
  border: 1px solid rgba(255, 190, 92, 0.36);
  border-radius: 4px;
  background: rgba(20, 13, 3, 0.82);
  color: #ffd28a;
  font-size: 11px;
  pointer-events: none;
  transform: translateX(-50%);
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
