<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import Map from 'ol/Map'
import Overlay from 'ol/Overlay'
import View from 'ol/View'
import Feature from 'ol/Feature'
import type MapBrowserEvent from 'ol/MapBrowserEvent'
import LineString from 'ol/geom/LineString'
import Point from 'ol/geom/Point'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import GeoJSON from 'ol/format/GeoJSON'
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style, Text } from 'ol/style'
import { defaults as defaultControls, Attribution } from 'ol/control'
import { defaults as defaultInteractions } from 'ol/interaction'
import { fromLonLat } from 'ol/proj'
import { buffer as bufferExtent, containsCoordinate } from 'ol/extent'
import 'ol/ol.css'
import { createBasemapLayer, DEFAULT_APP_BASEMAP } from '../../constants/mapBasemaps'
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../constants/mapDefaults'
import { bindMapInstance, useAppMapView } from '../../composables/useAppMapView'
import { useSimulationMap } from '../../composables/useSimulationMap'
import { useSimulationStore } from '../../composables/useSimulationStore'
import { useActiveIntersectionScene } from '../../composables/useActiveIntersectionScene'
import { TrafficModelRegistry } from '../../cesium/traffic/TrafficModelRegistry'
import { loadIntersectionTopologyCatalog, type IntersectionTopologyNode } from '../../mapv/intersectionTopology'
import { loadIntersectionTopologyRoutes } from '../../mapv/intersectionTopologyRoutes'
import { DISTURBANCE_EVENT_OPTIONS } from '../../constants/scenarioOptions'
import { useScenarioDraftStore, type ScenarioDraftDisturbanceEvent } from '../../composables/useScenarioDraftStore'
import { formatIntersectionLabel } from '../../utils/intersectionLabels'
import { buildDisturbanceWarningAggregates } from '../../utils/disturbanceWarnings'
import {
  resolveStableVehicleHeading,
  shortestAngleDelta,
  type VehicleHeadingState,
} from '../../mapv/vehicleOrientation'
import { SumoHeadingField } from '../../mapv/sumoHeadingTransform'
import DetectedEventOverlay from './DetectedEventOverlay.vue'
import type { CongestionLevel } from '../../types/intelligence'
import {
  CONGESTION_FLOW_COLORS,
  normalizeCongestionLevel,
} from '../../utils/topologyCongestion'
import {
  buildDirectedRouteCongestionLevels,
  loadEdgeTopologySegmentMap,
} from '../../utils/edgeTopologySegments'
import { expandDirectedTopologyRoutes } from '../../mapv/directedTopologyRoutes'
import { activeDetectedEventCards } from '../../utils/detectedEventDisplay'
import { loadFullRoadNetwork, releaseFullRoadNetwork } from '../../mapv/fullRoadNetwork'
import {
  disturbanceRuntimeStateLabel,
  disturbanceRuntimeTypeLabel,
  type DisturbanceRuntimeView,
} from '../../utils/runtimeDisturbances'
import {
  loadEventLanePositionIndex,
  resolveSessionEventMarkers,
  type EventLanePositionIndex,
} from '../../utils/eventLanePositionIndex'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })

const mapEl = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const { activeIntersectionId } = useActiveIntersectionScene()
const { geojson, error: networkError } = useSimulationMap(activeIntersectionId)
const {
  presentationTrafficView,
  snapshot,
  renderSessionRevision,
  runtimeDisturbances,
  unmappedRuntimeEvents,
} = useSimulationStore()
const { disturbanceEvents, simulationStartTime } = useScenarioDraftStore()

const warningPopupRef = ref<HTMLElement | null>(null)
const warningPopupTitle = ref('')
const warningPopupEvents = ref<Array<{ id: string; label: string; time: string }>>([])
const detectedEventCards = ref(activeDetectedEventCards(null))
const overlayViewToken = ref(0)

let map: Map | null = null
let resizeObserver: ResizeObserver | null = null
let warningOverlay: Overlay | null = null
let topologyNodes: IntersectionTopologyNode[] = []
let eventLanePositionIndex: EventLanePositionIndex | null = null
let edgeCongestionLevels: Record<string, CongestionLevel> = {}
let routeCongestionLevels: Record<string, CongestionLevel> = {}
const vehicleHeadingField = new SumoHeadingField((coordinate) => [
  coordinate[0],
  coordinate[1],
  coordinate[2],
])

function projectDetectedEventToOverlay(longitude: number, latitude: number): { x: number; y: number } | null {
  if (!map) return null
  const pixel = map.getPixelFromCoordinate(fromLonLat([longitude, latitude]))
  if (!pixel || !Number.isFinite(pixel[0]) || !Number.isFinite(pixel[1])) return null
  return { x: pixel[0], y: pixel[1] }
}

function bumpOverlayViewToken(): void {
  const zoom = map?.getView().getZoom() ?? DEFAULT_MAP_ZOOM
  const center = map?.getView().getCenter()
  overlayViewToken.value = Math.round(zoom * 100) * 1_000_000_000
    + Math.round((center?.[0] ?? 0) * 0.01)
    + Math.round((center?.[1] ?? 0) * 0.01)
}

function syncDetectedEventCards(): void {
  detectedEventCards.value = activeDetectedEventCards(snapshot.value?.event_detection?.cards)
}
const vehicleHeadingHistory = new globalThis.Map<string, VehicleHeadingState>()
interface AnimatedVehicleFeature {
  feature: Feature<Point>
  from: [number, number]
  to: [number, number]
  fromRotation: number
  toRotation: number
  startedAt: number
}
const vehicleFeatures = new globalThis.Map<string, AnimatedVehicleFeature>()
const vehicleMissingFrames = new globalThis.Map<string, number>()
let vehicleAnimationFrame: number | null = null
let vehicleInterpolationMs = 500
let lastVehicleSnapshotSequence = -1
let lastVehicleSnapshotArrivalMs: number | null = null
let vehicleSnapshotIntervalsMs: number[] = []
let snapVehiclePositions = false
let userViewportRevision = 0
let selectionUserViewportRevision = 0
let focusedIntersectionId: string | null = null
const MIN_VEHICLE_INTERPOLATION_MS = 500
const MAX_VEHICLE_INTERPOLATION_MS = 3_500
const VEHICLE_VIEWPORT_HYSTERESIS_METERS = 120

function percentile(values: number[], ratio: number): number {
  if (values.length === 0) return MIN_VEHICLE_INTERPOLATION_MS
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]
}

const networkSource = new VectorSource()
const fullRoadNetworkSource = new VectorSource()
const topologyFlowSource = new VectorSource()
const vehicleSource = new VectorSource()
const disturbanceSource = new VectorSource()
const geoJsonFormat = new GeoJSON()
const modelRegistry = new TrafficModelRegistry()

const fullRoadNetworkLayer = new VectorLayer({
  source: fullRoadNetworkSource,
  style: () => new Style({
    stroke: new Stroke({ color: 'rgba(41, 142, 255, 0.52)', width: 1.35 }),
  }),
  zIndex: 2,
})

const topologyBaseLayer = new VectorLayer({
  source: topologyFlowSource,
  style: () => new Style({
    stroke: new Stroke({ color: 'rgba(8, 125, 255, 0.28)', width: 5 }),
  }),
  zIndex: 3,
})

const topologyFlowLayer = new VectorLayer({
  source: topologyFlowSource,
  style: (feature) => {
    const routeId = String(feature.get('routeId') ?? '')
    const level = normalizeCongestionLevel(routeCongestionLevels[routeId] ?? 'free')
    return new Style({
      stroke: new Stroke({
        color: CONGESTION_FLOW_COLORS[level],
        width: level === 'free' ? 2.5 : 3.5,
      }),
    })
  },
  zIndex: 4,
})

function clearVehiclePresentation(): void {
  if (vehicleAnimationFrame !== null) cancelAnimationFrame(vehicleAnimationFrame)
  vehicleAnimationFrame = null
  vehicleSource.clear()
  vehicleFeatures.clear()
  vehicleHeadingHistory.clear()
  vehicleMissingFrames.clear()
  vehicleInterpolationMs = MIN_VEHICLE_INTERPOLATION_MS
  lastVehicleSnapshotSequence = -1
  lastVehicleSnapshotArrivalMs = null
  vehicleSnapshotIntervalsMs = []
  snapVehiclePositions = false
}

function clearSessionPresentation(): void {
  clearVehiclePresentation()
  detectedEventCards.value = []
  disturbanceSource.clear()
  warningOverlay?.setPosition(undefined)
  warningPopupTitle.value = ''
  warningPopupEvents.value = []
  edgeCongestionLevels = {}
  routeCongestionLevels = {}
  networkLayer.changed()
  topologyFlowLayer.changed()
}

const VEHICLE_RADIUS: Record<string, number> = {
  passenger: 6,
  truck: 8,
  bus: 9,
}

const networkLayer = new VectorLayer({
  source: networkSource,
  style: (feature) => {
    const type = feature.getGeometry()?.getType()
    if (type === 'Point') {
      return new Style({
        image: new CircleStyle({
          radius: 6,
          fill: new Fill({ color: 'rgba(33, 230, 255, 0.85)' }),
          stroke: new Stroke({ color: '#04121f', width: 2 }),
        }),
      })
    }
    const edgeId = String(feature.get('edge_id') ?? '')
    const level = normalizeCongestionLevel(edgeCongestionLevels[edgeId] ?? 'free')
    return new Style({
      stroke: new Stroke({
        color: level === 'free' ? 'rgba(90, 180, 255, 0.55)' : CONGESTION_FLOW_COLORS[level],
        width: level === 'free' ? 2 : 3,
      }),
    })
  },
  zIndex: 5,
})

const vehicleLayer = new VectorLayer({
  source: vehicleSource,
  style: (feature) => {
    const color = String(feature.get('color') ?? '#21e6ff')
    const vtype = String(feature.get('vtype') ?? 'passenger')
    const rotation = Number(feature.get('rotation') ?? 0)
    const radius = VEHICLE_RADIUS[vtype] ?? 6
    return new Style({
      image: new RegularShape({
        points: 3,
        radius,
        radius2: radius * 0.45,
        rotation,
        fill: new Fill({ color }),
        stroke: new Stroke({ color: 'rgba(4, 18, 31, 0.9)', width: 1 }),
      }),
    })
  },
  zIndex: 6,
})

const disturbanceLayer = new VectorLayer({
  source: disturbanceSource,
  style: (feature) => {
    const status = String(feature.get('status') ?? 'configured')
    const count = Number(feature.get('count') ?? 1)
    const opacity = status === 'completed' || status === 'cancelled' ? 0.42 : 1
    const fill = String(feature.get('color') ?? (
      status === 'completed' || status === 'cancelled' ? '#77858d' : '#d9152f'
    ))
    const triangle = new RegularShape({
      points: 3,
      radius: status === 'active' ? 18 : 16,
      angle: 0,
      fill: new Fill({ color: status === 'scheduled' ? 'rgba(4, 18, 31, 0.86)' : fill }),
      stroke: new Stroke({
        color: status === 'scheduled' ? '#ffbd59' : `rgba(255, 255, 255, ${opacity})`,
        width: 2,
      }),
    })
    triangle.setOpacity(opacity)
    const styles = [
      new Style({
        image: triangle,
      }),
      new Style({
        text: new Text({
          text: '!',
          font: '800 15px sans-serif',
          fill: new Fill({ color: `rgba(255, 255, 255, ${opacity})` }),
          offsetY: 2,
        }),
      }),
    ]
    if (count > 1) {
      styles.push(new Style({
        text: new Text({
          text: String(count),
          font: '700 10px sans-serif',
          fill: new Fill({ color: '#ffffff' }),
          backgroundFill: new Fill({ color: '#8c091c' }),
          backgroundStroke: new Stroke({ color: '#ffffff', width: 1 }),
          padding: [2, 4, 2, 4],
          offsetX: 14,
          offsetY: -13,
        }),
      }))
    }
    return styles
  },
  zIndex: 9,
})

function renderDisturbanceWarnings(): void {
  disturbanceSource.clear()
  if (topologyNodes.length === 0) return
  if (snapshot.value?.session_id) {
    if (runtimeDisturbances.value.length === 0) return
    const markers = resolveSessionEventMarkers(
      runtimeDisturbances.value,
      eventLanePositionIndex,
      topologyNodes,
      activeIntersectionId.value,
    )
    for (const marker of markers) {
      const events = marker.events
      const states = events.map((event) => event.state)
      const status = states.includes('FAILED')
        ? 'failed'
        : states.includes('ACTIVE')
          ? 'active'
          : states.every((state) => state === 'COMPLETED')
            ? 'completed'
            : states.every((state) => state === 'CANCELLED')
              ? 'cancelled'
              : 'scheduled'
      const feature = new Feature({
        geometry: new Point(fromLonLat([
          marker.position.longitude,
          marker.position.latitude,
        ])),
      })
      feature.setProperties({
        intersectionId: marker.position.intersectionId,
        events,
        count: events.length,
        status,
        color: status === 'failed'
          ? '#ff243f'
          : marker.color === 'orange' ? '#ff9f1a'
            : marker.color === 'blue' ? '#27b8ff' : '#d9152f',
        laneId: marker.position.laneId ?? '',
        positionSource: marker.position.source,
        fallbackReason: marker.position.fallbackReason ?? '',
        runtime: true,
      })
      disturbanceSource.addFeature(feature)
    }
    if (import.meta.env.DEV) {
      const diagnosticsWindow = window as Window & {
        __CITYPULSE_EVENT_MARKER_DIAGNOSTICS__?: Record<string, unknown>
      }
      diagnosticsWindow.__CITYPULSE_EVENT_MARKER_DIAGNOSTICS__ = {
        ...diagnosticsWindow.__CITYPULSE_EVENT_MARKER_DIAGNOSTICS__,
        sessionId: snapshot.value.session_id,
        twoDimensionalMarkerCount: markers.length,
        twoDimensionalEventCount: markers.reduce((sum, marker) => sum + marker.events.length, 0),
        positionFallbackCount: markers.filter((marker) => Boolean(marker.position.fallbackReason)).length,
        unmappedEventCount: unmappedRuntimeEvents.value.length,
      }
    }
    return
  }
  if (disturbanceEvents.value.length === 0) return
  const warnings = buildDisturbanceWarningAggregates(
    topologyNodes,
    disturbanceEvents.value,
    simulationStartTime.value,
    snapshot.value?.elapsed_seconds,
  )
  for (const warning of warnings) {
    const feature = new Feature({
      geometry: new Point(fromLonLat([warning.longitude, warning.latitude])),
    })
    feature.setProperties({
      intersectionId: warning.intersectionId,
      events: warning.events,
      count: warning.events.length,
      status: warning.status,
    })
    disturbanceSource.addFeature(feature)
  }
}

function syncEdgeCongestion(): void {
  const next: Record<string, CongestionLevel> = {}
  const edges = snapshot.value?.traffic_style?.edges ?? {}
  for (const [edgeId, style] of Object.entries(edges)) {
    next[edgeId] = normalizeCongestionLevel(style.level)
  }
  edgeCongestionLevels = next
  networkLayer.changed()
}

function syncTopologyCongestion(): void {
  const routeIds = topologyFlowSource.getFeatures().map((feature) => (
    String(feature.get('routeId') ?? '')
  )).filter(Boolean)
  routeCongestionLevels = buildDirectedRouteCongestionLevels(
    snapshot.value?.traffic_style,
    routeIds,
  )
  topologyFlowLayer.changed()
}

function refreshIntelligenceLayers(): void {
  syncDetectedEventCards()
  syncEdgeCongestion()
  syncTopologyCongestion()
}

async function loadTopologyOverview(): Promise<void> {
  const [nodes, routeManifest, , laneIndex] = await Promise.all([
    loadIntersectionTopologyCatalog(),
    loadIntersectionTopologyRoutes(),
    loadEdgeTopologySegmentMap().catch((cause: unknown) => {
      console.warn('[edge-topology] 2d map unavailable', cause)
      return {}
    }),
    loadEventLanePositionIndex().catch((cause: unknown) => {
      console.warn('[event-marker] lane position index unavailable', cause)
      return null
    }),
  ])
  topologyNodes = nodes
  eventLanePositionIndex = laneIndex
  vehicleHeadingField.setAnchors(nodes)
  topologyFlowSource.clear()
  for (const route of expandDirectedTopologyRoutes(routeManifest.routes)) {
    const feature = new Feature({
      geometry: new LineString(
        route.coordinates.map(([longitude, latitude]) => fromLonLat([longitude, latitude])),
      ),
    })
    feature.set('routeId', route.routeId)
    topologyFlowSource.addFeature(feature)
  }
  syncTopologyCongestion()
  renderDisturbanceWarnings()
  syncDetectedEventCards()
}

async function loadFullNetworkOverview(): Promise<void> {
  const collection = await loadFullRoadNetwork()
  const features = geoJsonFormat.readFeatures(collection, {
    dataProjection: 'EPSG:4326',
    featureProjection: 'EPSG:3857',
  })
  fullRoadNetworkSource.clear()
  releaseFullRoadNetwork()
  fullRoadNetworkSource.addFeatures(features)
}

function handleMapClick(event: MapBrowserEvent): void {
  if (!map) return
  if (!warningOverlay) return
  const feature = map.forEachFeatureAtPixel(event.pixel, (candidate, layer) => (
    layer === disturbanceLayer ? candidate : undefined
  ))
  if (!feature) {
    warningOverlay.setPosition(undefined)
    return
  }
  const intersectionId = String(feature.get('intersectionId') ?? '')
  const runtime = feature.get('runtime') === true
  const events = feature.get('events') as Array<ScenarioDraftDisturbanceEvent | DisturbanceRuntimeView>
  const laneId = String(feature.get('laneId') ?? '')
  const fallbackReason = String(feature.get('fallbackReason') ?? '')
  warningPopupTitle.value = [
    formatIntersectionLabel(intersectionId),
    laneId ? `车道 ${laneId}` : '',
    fallbackReason ? '位置为回退值' : '',
  ].filter(Boolean).join(' · ')
  warningPopupEvents.value = events.map((item) => runtime
    ? {
        id: (item as DisturbanceRuntimeView).eventId,
        label: `${disturbanceRuntimeTypeLabel((item as DisturbanceRuntimeView).eventType)} · ${disturbanceRuntimeStateLabel((item as DisturbanceRuntimeView).state)}`,
        time: (item as DisturbanceRuntimeView).error
          ?? `${(item as DisturbanceRuntimeView).startSeconds}s-${(item as DisturbanceRuntimeView).endSeconds}s`,
      }
    : {
        id: (item as ScenarioDraftDisturbanceEvent).event_id,
        label: DISTURBANCE_EVENT_OPTIONS.find((option) => option.value === (item as ScenarioDraftDisturbanceEvent).preset_id)?.label
          ?? (item as ScenarioDraftDisturbanceEvent).event_type,
        time: `${(item as ScenarioDraftDisturbanceEvent).start_time}-${(item as ScenarioDraftDisturbanceEvent).end_time}`,
      })
  warningOverlay.setPosition((feature.getGeometry() as Point).getCoordinates())
}

function renderNetwork() {
  networkSource.clear()
  const response = geojson.value
  if (!response) {
    return
  }
  const features = geoJsonFormat.readFeatures(response.geojson, {
    dataProjection: 'EPSG:4326',
    featureProjection: 'EPSG:3857',
  })
  networkSource.addFeatures(features)

  if (
    response.intersection_id !== activeIntersectionId.value
    || focusedIntersectionId === response.intersection_id
    || userViewportRevision !== selectionUserViewportRevision
  ) return
  const { west, south, east, north } = response.bounds
  mapView.fitBounds([west, south, east, north], `map:${response.intersection_id}`)
  focusedIntersectionId = response.intersection_id
}

function scheduleVehicleAnimation(): void {
  if (!props.active || vehicleAnimationFrame !== null) return
  vehicleAnimationFrame = requestAnimationFrame(animateVehicles)
}

function animateVehicles(now: number): void {
  vehicleAnimationFrame = null
  if (!props.active) return
  let pending = false
  for (const state of vehicleFeatures.values()) {
    const ratio = Math.min(1, Math.max(0, (now - state.startedAt) / vehicleInterpolationMs))
    state.feature.getGeometry()?.setCoordinates([
      state.from[0] + (state.to[0] - state.from[0]) * ratio,
      state.from[1] + (state.to[1] - state.from[1]) * ratio,
    ])
    state.feature.set(
      'rotation',
      state.fromRotation + shortestAngleDelta(state.fromRotation, state.toRotation) * ratio,
      true,
    )
    state.feature.changed()
    if (ratio < 1) pending = true
  }
  if (pending) scheduleVehicleAnimation()
}

function renderVehicles() {
  if (!props.active) return
  const vehicles = presentationTrafficView.value?.vehicles ?? []
  const activeIds = new Set<string>()
  const now = performance.now()
  const sequence = snapshot.value?.sequence ?? -1
  const isNewSourceSnapshot = sequence !== lastVehicleSnapshotSequence
  if (isNewSourceSnapshot) {
    if (lastVehicleSnapshotArrivalMs != null) {
      const interval = now - lastVehicleSnapshotArrivalMs
      if (interval > 0 && interval < 10_000) {
        vehicleSnapshotIntervalsMs.push(interval)
        vehicleSnapshotIntervalsMs = vehicleSnapshotIntervalsMs.slice(-30)
        vehicleInterpolationMs = Math.min(
          MAX_VEHICLE_INTERPOLATION_MS,
          Math.max(MIN_VEHICLE_INTERPOLATION_MS, percentile(vehicleSnapshotIntervalsMs, .95)),
        )
      }
    }
    lastVehicleSnapshotSequence = sequence
    lastVehicleSnapshotArrivalMs = now
  }
  const mapSize = map?.getSize()
  const visibleExtent = map && mapSize
    ? bufferExtent(map.getView().calculateExtent(mapSize), VEHICLE_VIEWPORT_HYSTERESIS_METERS)
    : null
  for (const vehicle of vehicles) {
    if (vehicle.longitude == null || vehicle.latitude == null) {
      continue
    }
    const target = fromLonLat([vehicle.longitude, vehicle.latitude]) as [number, number]
    if (visibleExtent && !containsCoordinate(visibleExtent, target)) continue
    const definition = modelRegistry.resolve(vehicle.vehicle_id, vehicle.lane_id)
    const previousHeading = vehicleHeadingHistory.get(vehicle.vehicle_id) ?? null
    const headingResolution = vehicleHeadingField.resolve(
      vehicle.angle,
      [vehicle.longitude, vehicle.latitude],
    )
    if (!headingResolution && !previousHeading) continue
    const resolvedHeading = resolveStableVehicleHeading({
      sourceMapHeading: headingResolution?.heading,
      speedMetersPerSecond: vehicle.speed,
      current: { longitude: vehicle.longitude, latitude: vehicle.latitude },
      timeSeconds: presentationTrafficView.value?.elapsed_seconds ?? 0,
    }, previousHeading)
    vehicleHeadingHistory.set(vehicle.vehicle_id, resolvedHeading.state)
    activeIds.add(vehicle.vehicle_id)
    vehicleMissingFrames.delete(vehicle.vehicle_id)
    const existing = vehicleFeatures.get(vehicle.vehicle_id)
    const feature = existing?.feature ?? new Feature<Point>({ geometry: new Point(target) })
    feature.set('color', definition.color)
    feature.set('vtype', definition.type)
    const targetRotation = Math.PI / 2 - resolvedHeading.heading
    if (existing) {
      if (snapVehiclePositions || presentationTrafficView.value != null) {
        existing.from = target
        existing.to = target
        existing.fromRotation = targetRotation
        existing.toRotation = targetRotation
        existing.startedAt = now
        feature.getGeometry()?.setCoordinates(target)
        feature.set('rotation', targetRotation)
        feature.changed()
        continue
      }
      const current = feature.getGeometry()?.getCoordinates() as [number, number] | undefined
      existing.from = current ? [...current] as [number, number] : target
      existing.to = target
      existing.fromRotation = Number(feature.get('rotation') ?? targetRotation)
      existing.toRotation = targetRotation
      existing.startedAt = now
    } else {
      feature.set('rotation', targetRotation)
      vehicleSource.addFeature(feature)
      vehicleFeatures.set(vehicle.vehicle_id, {
        feature,
        from: target,
        to: target,
        fromRotation: targetRotation,
        toRotation: targetRotation,
        startedAt: now,
      })
    }
  }
  for (const id of vehicleHeadingHistory.keys()) {
    if (activeIds.has(id)) continue
    vehicleMissingFrames.delete(id)
    vehicleHeadingHistory.delete(id)
    const state = vehicleFeatures.get(id)
    if (state) vehicleSource.removeFeature(state.feature)
    vehicleFeatures.delete(id)
  }
  snapVehiclePositions = false
  scheduleVehicleAnimation()
}

function markUserViewportInteraction(): void {
  userViewportRevision += 1
}

function syncMapViewportDiagnostics(): void {
  const center = map?.getView().getCenter()
  if (!mapEl.value || !center) return
  mapEl.value.dataset.mapCenter = center.map((value) => value.toFixed(3)).join(',')
  mapEl.value.dataset.mapZoom = String(map?.getView().getZoom() ?? '')
}

function handleMapMoveEnd(): void {
  syncMapViewportDiagnostics()
  bumpOverlayViewToken()
  renderVehicles()
}

function focusActiveIntersection(expectedUserRevision?: number): void {
  if (expectedUserRevision !== undefined && userViewportRevision !== expectedUserRevision) return
  const node = topologyNodes.find((item) => item.intersectionId === activeIntersectionId.value)
  if (!node) return
  mapView.focusIntersection(
    [node.longitude, node.latitude],
    node.intersectionId,
    { force: true, duration: 700 },
  )
  focusedIntersectionId = node.intersectionId
}

onMounted(() => {
  if (!mapEl.value) {
    return
  }

  map = new Map({
    target: mapEl.value,
    layers: [
      createBasemapLayer(DEFAULT_APP_BASEMAP),
      fullRoadNetworkLayer,
      topologyBaseLayer,
      topologyFlowLayer,
      networkLayer,
      vehicleLayer,
      disturbanceLayer,
    ],
    view: new View({
      center: fromLonLat(DEFAULT_MAP_CENTER),
      zoom: DEFAULT_MAP_ZOOM,
    }),
    controls: defaultControls({
      attribution: false,
      zoom: false,
      rotate: false,
    }).extend([
      new Attribution({
        collapsible: false,
      }),
    ]),
    interactions: defaultInteractions(),
  })

  bindMapInstance(mapView, map)
  if (warningPopupRef.value) {
    warningOverlay = new Overlay({
      element: warningPopupRef.value,
      positioning: 'bottom-center',
      offset: [0, -20],
      stopEvent: true,
    })
    map.addOverlay(warningOverlay)
  }
  map.on('singleclick', handleMapClick)
  map.on('pointerdrag', markUserViewportInteraction)
  map.getViewport().addEventListener('wheel', markUserViewportInteraction, { passive: true })
  map.on('moveend', handleMapMoveEnd)
  map.getView().on('change:center', bumpOverlayViewToken)
  map.getView().on('change:resolution', bumpOverlayViewToken)
  bumpOverlayViewToken()
  syncMapViewportDiagnostics()
  renderNetwork()
  renderVehicles()
  refreshIntelligenceLayers()
  const topologyLoadUserRevision = userViewportRevision
  void loadTopologyOverview()
    .then(() => focusActiveIntersection(topologyLoadUserRevision))
    .catch((cause: unknown) => console.warn('[detected-event] 2d topology unavailable', cause))
  void loadFullNetworkOverview()
    .catch((cause: unknown) => console.warn('[full-road-network] 2d map unavailable', cause))

  resizeObserver = new ResizeObserver(() => {
    map?.updateSize()
  })
  resizeObserver.observe(mapEl.value)
})

watch(geojson, renderNetwork)
watch(presentationTrafficView, renderVehicles)
watch(renderSessionRevision, clearSessionPresentation, { flush: 'sync' })
watch(() => props.active, (active) => {
  if (active) {
    map?.updateSize()
    snapVehiclePositions = true
    renderVehicles()
    refreshIntelligenceLayers()
    map?.renderSync()
  } else if (vehicleAnimationFrame !== null) {
    cancelAnimationFrame(vehicleAnimationFrame)
    vehicleAnimationFrame = null
  }
})
watch(activeIntersectionId, () => {
  selectionUserViewportRevision = userViewportRevision
  focusedIntersectionId = null
  focusActiveIntersection()
})
watch([disturbanceEvents, runtimeDisturbances, snapshot, simulationStartTime], renderDisturbanceWarnings, { deep: true })
watch(snapshot, refreshIntelligenceLayers)


onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  map?.un('singleclick', handleMapClick)
  map?.un('pointerdrag', markUserViewportInteraction)
  map?.un('moveend', handleMapMoveEnd)
  map?.getViewport().removeEventListener('wheel', markUserViewportInteraction)
  warningOverlay?.setPosition(undefined)
  warningOverlay = null
  topologyNodes = []
  eventLanePositionIndex = null
  topologyFlowSource.clear()
  fullRoadNetworkSource.clear()
  routeCongestionLevels = {}
  clearVehiclePresentation()
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="app-background-map">
    <div ref="mapEl" class="app-background-map__canvas" role="application" aria-label="二维交通路网地图" />
    <DetectedEventOverlay
      :cards="detectedEventCards"
      :snapshot="snapshot"
      :project="projectDetectedEventToOverlay"
      :active="props.active"
      :view-token="overlayViewToken"
    />
    <div v-if="networkError" class="app-background-map__network-status">
      当前路口路网加载失败，已保留深色底图定位
    </div>
    <div v-if="props.active && unmappedRuntimeEvents.length" class="app-background-map__runtime-warning">
      {{ unmappedRuntimeEvents.length }} 个扰动事件的位置无法从旧会话恢复
    </div>
    <div ref="warningPopupRef" class="disturbance-warning-popup">
      <strong>{{ warningPopupTitle }}</strong>
      <div v-for="event in warningPopupEvents" :key="event.id">
        <span>{{ event.label }}</span>
        <time>{{ event.time }}</time>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-background-map {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
}

.app-background-map__canvas {
  width: 100%;
  height: 100%;
}

.app-background-map__network-status {
  position: absolute;
  left: 50%;
  bottom: 72px;
  z-index: 10;
  padding: 6px 10px;
  border: 1px solid rgba(255, 190, 92, 0.42);
  border-radius: 4px;
  background: rgba(20, 13, 3, 0.86);
  color: #ffd28a;
  font-size: 11px;
  pointer-events: none;
  transform: translateX(-50%);
}

.app-background-map__runtime-warning {
  position: absolute;
  right: 24px;
  bottom: 62px;
  z-index: 10;
  max-width: 320px;
  padding: 7px 10px;
  border: 1px solid rgba(255, 183, 77, 0.55);
  border-radius: 6px;
  background: rgba(4, 20, 29, 0.9);
  color: #ffd28c;
  font-size: 12px;
  pointer-events: none;
}

.app-background-map__canvas :deep(.ol-attribution) {
  display: none;
}

.disturbance-warning-popup {
  min-width: 180px;
  display: grid;
  gap: 7px;
  padding: 11px 13px;
  border: 1px solid rgba(255, 90, 108, .78);
  border-radius: 4px;
  background: rgba(28, 7, 18, .94);
  box-shadow: 0 10px 26px rgba(0, 0, 0, .45);
  color: #fff;
  font: 12px/1.4 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.disturbance-warning-popup:empty { display: none; }
.disturbance-warning-popup strong { color: #ff9aaa; font-size: 13px; }
.disturbance-warning-popup div { display: flex; justify-content: space-between; gap: 14px; }
.disturbance-warning-popup time { color: #ffc3ca; white-space: nowrap; }

</style>
