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
import { Circle as CircleStyle, Fill, Icon, RegularShape, Stroke, Style, Text } from 'ol/style'
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
  type VehicleHeadingState,
} from '../../mapv/vehicleOrientation'
import { SumoHeadingField } from '../../mapv/sumoHeadingTransform'
import type { CongestionLevel, TrafficStylePayload } from '../../types/intelligence'
import {
  CONGESTION_FLOW_COLORS,
  normalizeCongestionLevel,
} from '../../utils/topologyCongestion'
import {
  buildDirectedRouteCongestionLevels,
  loadEdgeTopologySegmentMap,
} from '../../utils/edgeTopologySegments'
import { expandDirectedTopologyRoutes } from '../../mapv/directedTopologyRoutes'
import {
  DETECTED_EVENT_ICON_URL,
  activeDetectedEventCards,
  detectedEventClockTime,
  detectedEventDurationSeconds,
  detectedEventTypeLabel,
  formatDetectedEventDuration,
} from '../../utils/detectedEventDisplay'
import { groupDetectedEventMapMarkers } from '../../utils/detectedEventMapMarkers'
import type { DetectedEventCard } from '../../types/intelligence'
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
import {
  sharedLaneCongestionStateResolver,
  type LaneCongestionStateSnapshot,
} from '../../utils/laneCongestionState'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })

const mapEl = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const { activeIntersectionId } = useActiveIntersectionScene()
const { geojson, error: networkError } = useSimulationMap(activeIntersectionId)
const {
  presentationTrafficView,
  vehiclePresentationDiagnostics,
  snapshot,
  renderSessionRevision,
  simulationPresentationGeneration,
  runtimeDisturbances,
  unmappedRuntimeEvents,
} = useSimulationStore()
const { disturbanceEvents, simulationStartTime } = useScenarioDraftStore()

const warningPopupRef = ref<HTMLElement | null>(null)
const warningPopupTitle = ref('')
const warningPopupEvents = ref<Array<{ id: string; label: string; time: string }>>([])
const detectedEventCards = ref(activeDetectedEventCards(null))
const detectedPopupRef = ref<HTMLElement | null>(null)
const detectedPopupCards = ref<DetectedEventCard[]>([])

let map: Map | null = null
let resizeObserver: ResizeObserver | null = null
let warningOverlay: Overlay | null = null
let detectedOverlay: Overlay | null = null
let topologyNodes: IntersectionTopologyNode[] = []
let eventLanePositionIndex: EventLanePositionIndex | null = null
let laneCongestionSnapshot: LaneCongestionStateSnapshot | null = null
let routeCongestionLevels: Record<string, CongestionLevel> = {}
const vehicleHeadingField = new SumoHeadingField((coordinate) => [
  coordinate[0],
  coordinate[1],
  coordinate[2],
])

function syncDetectedEventCards(): void {
  detectedEventCards.value = activeDetectedEventCards(snapshot.value?.event_detection?.cards)
  detectedEventSource.clear()
  const activeSessionId = snapshot.value?.session_id ?? ''
  for (const marker of groupDetectedEventMapMarkers(detectedEventCards.value)) {
    const feature = new Feature({
      geometry: new Point(fromLonLat([marker.longitude, marker.latitude])),
    })
    feature.setProperties({
      markerId: marker.id,
      cards: marker.cards,
      count: marker.cards.length,
      sessionId: activeSessionId,
      sessionGeneration: simulationPresentationGeneration.value,
    })
    detectedEventSource.addFeature(feature)
  }
  if (import.meta.env.DEV) {
    const diagnosticsWindow = window as Window & {
      __CITYPULSE_EVENT_MARKER_DIAGNOSTICS__?: Record<string, unknown>
    }
    diagnosticsWindow.__CITYPULSE_EVENT_MARKER_DIAGNOSTICS__ = {
      ...diagnosticsWindow.__CITYPULSE_EVENT_MARKER_DIAGNOSTICS__,
      detectedEventMapMarkerCount: detectedEventSource.getFeatures().length,
      detectedEventMapEventCount: detectedEventCards.value.length,
      presentationGeneration: simulationPresentationGeneration.value,
    }
  }
  if (detectedPopupCards.value.length > 0) {
    const activeIds = new Set(detectedEventCards.value.map((card) => card.event_id))
    if (!detectedPopupCards.value.some((card) => activeIds.has(card.event_id))) {
      closeDetectedPopup()
    }
  }
}
const vehicleHeadingHistory = new globalThis.Map<string, VehicleHeadingState>()
interface AnimatedVehicleFeature {
  feature: Feature<Point>
}
const vehicleFeatures = new globalThis.Map<string, AnimatedVehicleFeature>()
const vehicleMissingFrames = new globalThis.Map<string, number>()
let userViewportRevision = 0
let selectionUserViewportRevision = 0
let focusedIntersectionId: string | null = null
const VEHICLE_VIEWPORT_HYSTERESIS_METERS = 120

const networkSource = new VectorSource()
const fullRoadNetworkSource = new VectorSource()
const topologyFlowSource = new VectorSource()
const laneCongestionSource = new VectorSource()
const vehicleSource = new VectorSource()
const disturbanceSource = new VectorSource()
const detectedEventSource = new VectorSource()
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
  vehicleSource.clear()
  vehicleFeatures.clear()
  vehicleHeadingHistory.clear()
  vehicleMissingFrames.clear()
}

function clearSessionPresentation(): void {
  clearVehiclePresentation()
  detectedEventCards.value = []
  detectedEventSource.clear()
  disturbanceSource.clear()
  closeDetectedPopup()
  warningOverlay?.setPosition(undefined)
  warningPopupTitle.value = ''
  warningPopupEvents.value = []
  laneCongestionSnapshot = null
  laneCongestionSource.clear()
  routeCongestionLevels = {}
  topologyFlowLayer.changed()
}

const VEHICLE_RADIUS: Record<string, number> = {
  passenger: 6,
  truck: 8,
  bus: 9,
  electric_bicycle: 4,
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
    return new Style({
      stroke: new Stroke({
        color: 'rgba(90, 180, 255, 0.55)',
        width: 2,
      }),
    })
  },
  zIndex: 5,
})

const laneCongestionLayer = new VectorLayer({
  source: laneCongestionSource,
  style: (feature) => {
    const level = normalizeCongestionLevel(feature.get('congestionLevel'))
    return new Style({
      stroke: new Stroke({
        color: CONGESTION_FLOW_COLORS[level],
        width: level === 'severe' ? 4 : level === 'congested' ? 3.5 : 3,
      }),
    })
  },
  zIndex: 5.5,
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

const detectedEventLayer = new VectorLayer({
  source: detectedEventSource,
  declutter: true,
  style: (feature) => {
    const count = Number(feature.get('count') ?? 1)
    const styles = [new Style({
      image: new Icon({
        src: DETECTED_EVENT_ICON_URL,
        anchor: [0.5, 1],
        anchorXUnits: 'fraction',
        anchorYUnits: 'fraction',
        width: 34,
        height: 34,
      }),
    })]
    if (count > 1) {
      styles.push(new Style({
        text: new Text({
          text: String(count),
          font: '700 10px sans-serif',
          fill: new Fill({ color: '#ffffff' }),
          backgroundFill: new Fill({ color: '#8c091c' }),
          backgroundStroke: new Stroke({ color: '#ffffff', width: 1 }),
          padding: [2, 4, 2, 4],
          offsetX: 13,
          offsetY: -28,
        }),
      }))
    }
    return styles
  },
  zIndex: 10,
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

function syncLaneCongestion(): void {
  laneCongestionSource.clear()
  const current = snapshot.value
  if (!current) {
    laneCongestionSnapshot = null
    return
  }
  laneCongestionSnapshot = sharedLaneCongestionStateResolver.resolve({
    sessionId: current.session_id,
    presentationGeneration: simulationPresentationGeneration.value,
    sequence: current.sequence,
    asOfSeconds: current.elapsed_seconds,
    intersections: current.intersections,
    laneEntries: eventLanePositionIndex?.entries,
  })
  const entriesByLaneId = new globalThis.Map(
    eventLanePositionIndex?.entries.map((entry) => [entry.laneId, entry] as const) ?? [],
  )
  for (const state of Object.values(laneCongestionSnapshot.lanes)) {
    if (state.level === 'free') continue
    const entry = entriesByLaneId.get(state.laneId)
    if (!entry || entry.kind !== 'driving') continue
    const feature = new Feature({
      geometry: new LineString(entry.coordinates.map((coordinate) => fromLonLat(coordinate))),
    })
    feature.setProperties({
      laneId: state.laneId,
      edgeId: state.edgeId,
      congestionLevel: state.level,
      meanSpeed: state.meanSpeed,
    })
    laneCongestionSource.addFeature(feature)
  }
  if (import.meta.env.DEV) {
    const diagnosticsWindow = window as Window & {
      __CITYPULSE_LANE_CONGESTION_DIAGNOSTICS__?: Record<string, unknown>
    }
    diagnosticsWindow.__CITYPULSE_LANE_CONGESTION_DIAGNOSTICS__ = {
      ...laneCongestionSnapshot.diagnostics,
      twoDimensionalFlowLaneCount: laneCongestionSource.getFeatures().length,
      sessionId: current.session_id,
      sequence: current.sequence,
    }
  }
}

function syncTopologyCongestion(): void {
  const routeIds = topologyFlowSource.getFeatures().map((feature) => (
    String(feature.get('routeId') ?? '')
  )).filter(Boolean)
  const trafficStyle = snapshot.value?.traffic_style
  const laneEdges = laneCongestionSnapshot?.edgeIdsWithLaneMetrics ?? new Set<string>()
  const fallbackTrafficStyle: TrafficStylePayload | null = trafficStyle
    ? {
        ...trafficStyle,
        edges: Object.fromEntries(Object.entries(trafficStyle.edges).filter(([edgeId]) => (
          !laneEdges.has(edgeId)
        ))),
      }
    : null
  routeCongestionLevels = buildDirectedRouteCongestionLevels(fallbackTrafficStyle, routeIds)
  topologyFlowLayer.changed()
}

function refreshIntelligenceLayers(): void {
  syncDetectedEventCards()
  syncLaneCongestion()
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
  syncLaneCongestion()
  syncTopologyCongestion()
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
  const detectedFeature = map.forEachFeatureAtPixel(event.pixel, (candidate, layer) => (
    layer === detectedEventLayer ? candidate : undefined
  ))
  if (detectedFeature) {
    const featureGeneration = Number(detectedFeature.get('sessionGeneration'))
    const featureSessionId = String(detectedFeature.get('sessionId') ?? '')
    if (
      featureGeneration === simulationPresentationGeneration.value
      && featureSessionId === (snapshot.value?.session_id ?? '')
    ) {
      detectedPopupCards.value = detectedFeature.get('cards') as DetectedEventCard[]
      detectedOverlay?.setPosition((detectedFeature.getGeometry() as Point).getCoordinates())
      warningOverlay?.setPosition(undefined)
      return
    }
  }
  closeDetectedPopup()
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

function closeDetectedPopup(): void {
  detectedOverlay?.setPosition(undefined)
  detectedPopupCards.value = []
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

function renderVehicles() {
  if (!props.active) return
  const vehicles = presentationTrafficView.value?.vehicles ?? []
  const activeIds = new Set<string>()
  const addedFeatures: Feature<Point>[] = []
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
    const definition = modelRegistry.resolve(vehicle.vehicle_id, vehicle.lane_id, vehicle.type_id)
    const previousHeading = vehicleHeadingHistory.get(vehicle.vehicle_id) ?? null
    const canonicalHeading = Number(vehicle.canonical_heading_radians)
    const headingResolution = Number.isFinite(canonicalHeading)
      ? { heading: canonicalHeading, anchorIds: [], local: null }
      : vehicleHeadingField.resolve(vehicle.angle, [vehicle.longitude, vehicle.latitude])
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
    if (feature.get('color') !== definition.color) feature.set('color', definition.color)
    if (feature.get('vtype') !== definition.type) feature.set('vtype', definition.type)
    const targetRotation = Math.PI / 2 - resolvedHeading.heading
    if (existing) {
      const geometry = feature.getGeometry()
      const current = geometry?.getCoordinates()
      if (!current || Math.hypot(current[0] - target[0], current[1] - target[1]) > 0.002) {
        geometry?.setCoordinates(target)
      }
      if (Math.abs(Number(feature.get('rotation') ?? 0) - targetRotation) > 1e-5) {
        feature.set('rotation', targetRotation)
      }
    } else {
      feature.set('rotation', targetRotation)
      addedFeatures.push(feature)
      vehicleFeatures.set(vehicle.vehicle_id, { feature })
    }
  }
  if (addedFeatures.length > 0) vehicleSource.addFeatures(addedFeatures)
  for (const id of vehicleHeadingHistory.keys()) {
    if (activeIds.has(id)) continue
    vehicleMissingFrames.delete(id)
    vehicleHeadingHistory.delete(id)
    const state = vehicleFeatures.get(id)
    if (state) vehicleSource.removeFeature(state.feature)
    vehicleFeatures.delete(id)
  }
  if (import.meta.env.DEV) {
    const diagnosticsWindow = window as Window & {
      __CITYPULSE_2D_VEHICLE_DIAGNOSTICS__?: Record<string, unknown>
    }
    diagnosticsWindow.__CITYPULSE_2D_VEHICLE_DIAGNOSTICS__ = {
      sessionId: presentationTrafficView.value?.session_id ?? '',
      displayElapsedSeconds: presentationTrafficView.value?.elapsed_seconds ?? null,
      authoritativeVehicleCount: vehiclePresentationDiagnostics.value.authoritativeVehicleCount,
      canonicalVehicleCount: vehiclePresentationDiagnostics.value.canonicalVehicleCount,
      unresolvedVehicleCount: vehiclePresentationDiagnostics.value.unresolvedVehicleCount,
      renderedVehicleCount: activeIds.size,
      authoritativeVehicleIds: vehicles.map((vehicle) => vehicle.vehicle_id),
      renderedVehicleIds: [...activeIds],
      canonicalLaneGeometryAvailable: eventLanePositionIndex?.laneCount ?? 0,
      canonicalMotion: vehiclePresentationDiagnostics.value.motionAudit,
      capturedAt: new Date().toISOString(),
    }
  }
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
      laneCongestionLayer,
      vehicleLayer,
      disturbanceLayer,
      detectedEventLayer,
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
  if (detectedPopupRef.value) {
    detectedOverlay = new Overlay({
      element: detectedPopupRef.value,
      positioning: 'bottom-center',
      offset: [0, -38],
      stopEvent: true,
      autoPan: { animation: { duration: 180 }, margin: 18 },
    })
    map.addOverlay(detectedOverlay)
  }
  map.on('singleclick', handleMapClick)
  map.on('pointerdrag', markUserViewportInteraction)
  map.getViewport().addEventListener('wheel', markUserViewportInteraction, { passive: true })
  map.on('moveend', handleMapMoveEnd)
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
    renderVehicles()
    refreshIntelligenceLayers()
    map?.renderSync()
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
  detectedOverlay?.setPosition(undefined)
  detectedOverlay = null
  topologyNodes = []
  eventLanePositionIndex = null
  topologyFlowSource.clear()
  laneCongestionSource.clear()
  fullRoadNetworkSource.clear()
  routeCongestionLevels = {}
  detectedEventSource.clear()
  clearVehiclePresentation()
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="app-background-map">
    <div ref="mapEl" class="app-background-map__canvas" role="application" aria-label="二维交通路网地图" />
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
    <section v-show="detectedPopupCards.length" ref="detectedPopupRef" class="detected-event-map-popup" aria-label="事件识别详情">
      <header>
        <strong>事件识别</strong>
        <button type="button" title="关闭" aria-label="关闭事件识别详情" @click="closeDetectedPopup">x</button>
      </header>
      <article v-for="card in detectedPopupCards" :key="card.event_id">
        <strong>{{ detectedEventTypeLabel(card) }}</strong>
        <span>{{ detectedEventClockTime(snapshot, card.start_seconds) }}</span>
        <span>持续 {{ formatDetectedEventDuration(detectedEventDurationSeconds(snapshot, card)) }}</span>
        <span>{{ card.intersection_id }} / {{ card.lane_ids.join('、') || '--' }}</span>
      </article>
    </section>
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

.detected-event-map-popup {
  width: min(300px, calc(100vw - 32px));
  max-height: min(360px, calc(100vh - 32px));
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid rgba(82, 194, 250, 0.58);
  border-radius: 6px;
  background: rgba(3, 20, 31, 0.97);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.52);
  color: #eaf8ff;
}

.detected-event-map-popup header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.detected-event-map-popup header button { width: 24px; height: 24px; padding: 0; border: 0; background: transparent; color: #eaf8ff; cursor: pointer; }
.detected-event-map-popup article { display: grid; gap: 3px; padding: 8px 0; border-top: 1px solid rgba(120, 202, 235, 0.18); font-size: 12px; }
.detected-event-map-popup article:first-of-type { margin-top: 7px; }
.detected-event-map-popup article span { color: #a8d9ea; }

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
