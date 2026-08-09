<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import Map from 'ol/Map'
import Overlay from 'ol/Overlay'
import View from 'ol/View'
import Feature from 'ol/Feature'
import type MapBrowserEvent from 'ol/MapBrowserEvent'
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
import { DISTURBANCE_EVENT_OPTIONS } from '../../constants/scenarioOptions'
import { useScenarioDraftStore, type ScenarioDraftDisturbanceEvent } from '../../composables/useScenarioDraftStore'
import { formatIntersectionLabel } from '../../utils/intersectionLabels'
import { buildDisturbanceWarningAggregates } from '../../utils/disturbanceWarnings'
import {
  resolveStableVehicleHeading,
  shortestAngleDelta,
  type VehicleHeadingState,
} from '../../mapv/vehicleOrientation'
import type { CongestionLevel } from '../../types/intelligence'
import {
  CONGESTION_FLOW_COLORS,
  normalizeCongestionLevel,
} from '../../utils/topologyCongestion'
import {
  DETECTED_EVENT_ICON_URL,
  activeDetectedEventCards,
  detectedEventClockTime,
  detectedEventFlowSummary,
  detectedEventTypeLabel,
} from '../../utils/detectedEventDisplay'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })

const mapEl = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const { activeIntersectionId } = useActiveIntersectionScene()
const { geojson, error: networkError } = useSimulationMap(activeIntersectionId)
const { trafficView, snapshot, renderSessionRevision } = useSimulationStore()
const { disturbanceEvents, simulationStartTime } = useScenarioDraftStore()

const warningPopupRef = ref<HTMLElement | null>(null)
const warningPopupTitle = ref('')
const warningPopupEvents = ref<Array<{ id: string; label: string; time: string }>>([])
const detectedPopupRef = ref<HTMLElement | null>(null)
const detectedPopupClock = ref('')
const detectedPopupType = ref('')
const detectedPopupFlow = ref('')

let map: Map | null = null
let resizeObserver: ResizeObserver | null = null
let warningOverlay: Overlay | null = null
let detectedOverlay: Overlay | null = null
let topologyNodes: IntersectionTopologyNode[] = []
let edgeCongestionLevels: Record<string, CongestionLevel> = {}
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
const MIN_VEHICLE_INTERPOLATION_MS = 500
const MAX_VEHICLE_INTERPOLATION_MS = 3_500
const VEHICLE_VIEWPORT_HYSTERESIS_METERS = 120

function percentile(values: number[], ratio: number): number {
  if (values.length === 0) return MIN_VEHICLE_INTERPOLATION_MS
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]
}

const networkSource = new VectorSource()
const vehicleSource = new VectorSource()
const disturbanceSource = new VectorSource()
const detectedSource = new VectorSource()
const geoJsonFormat = new GeoJSON()
const modelRegistry = new TrafficModelRegistry()

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
    const opacity = status === 'completed' ? 0.42 : 1
    const fill = status === 'active' ? '#ff243f' : '#d9152f'
    const triangle = new RegularShape({
      points: 3,
      radius: status === 'active' ? 18 : 16,
      angle: 0,
      fill: new Fill({ color: fill }),
      stroke: new Stroke({ color: `rgba(255, 255, 255, ${opacity})`, width: 2 }),
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

const detectedLayer = new VectorLayer({
  source: detectedSource,
  style: () => new Style({
    image: new Icon({
      src: DETECTED_EVENT_ICON_URL,
      anchor: [0.5, 0.92],
      scale: 0.55,
    }),
  }),
  zIndex: 10,
})

function renderDisturbanceWarnings(): void {
  disturbanceSource.clear()
  if (topologyNodes.length === 0 || disturbanceEvents.value.length === 0) return
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

function renderDetectedEvents(): void {
  detectedSource.clear()
  detectedOverlay?.setPosition(undefined)
  const cards = activeDetectedEventCards(snapshot.value?.event_detection?.cards)
  for (const card of cards) {
    const feature = new Feature({
      geometry: new Point(fromLonLat([Number(card.longitude), Number(card.latitude)])),
    })
    feature.setProperties({
      kind: 'detected_event',
      eventId: card.event_id,
      clock: detectedEventClockTime(snapshot.value, card.start_seconds),
      typeLabel: detectedEventTypeLabel(card),
      flowSummary: detectedEventFlowSummary(card),
    })
    detectedSource.addFeature(feature)
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

function handleMapClick(event: MapBrowserEvent): void {
  if (!map) return
  const detectedFeature = map.forEachFeatureAtPixel(event.pixel, (candidate, layer) => (
    layer === detectedLayer ? candidate : undefined
  ))
  if (detectedFeature && detectedOverlay) {
    warningOverlay?.setPosition(undefined)
    detectedPopupClock.value = String(detectedFeature.get('clock') ?? '')
    detectedPopupType.value = String(detectedFeature.get('typeLabel') ?? '')
    detectedPopupFlow.value = String(detectedFeature.get('flowSummary') ?? '')
    detectedOverlay.setPosition((detectedFeature.getGeometry() as Point).getCoordinates())
    return
  }
  detectedOverlay?.setPosition(undefined)
  if (!warningOverlay) return
  const feature = map.forEachFeatureAtPixel(event.pixel, (candidate, layer) => (
    layer === disturbanceLayer ? candidate : undefined
  ))
  if (!feature) {
    warningOverlay.setPosition(undefined)
    return
  }
  const intersectionId = String(feature.get('intersectionId') ?? '')
  const events = feature.get('events') as ScenarioDraftDisturbanceEvent[]
  warningPopupTitle.value = formatIntersectionLabel(intersectionId)
  warningPopupEvents.value = events.map((item) => ({
    id: item.event_id,
    label: DISTURBANCE_EVENT_OPTIONS.find((option) => option.value === item.preset_id)?.label
      ?? item.event_type,
    time: `${item.start_time}-${item.end_time}`,
  }))
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

  const { west, south, east, north } = response.bounds
  mapView.fitBounds([west, south, east, north], `map:${response.intersection_id}`)
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
  const vehicles = trafficView.value?.vehicles ?? []
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
    const resolvedHeading = resolveStableVehicleHeading({
      sumoAngleDegrees: vehicle.angle,
      speedMetersPerSecond: vehicle.speed,
      current: { longitude: vehicle.longitude, latitude: vehicle.latitude },
      timeSeconds: trafficView.value?.elapsed_seconds ?? 0,
    }, vehicleHeadingHistory.get(vehicle.vehicle_id) ?? null)
    vehicleHeadingHistory.set(vehicle.vehicle_id, resolvedHeading.state)
    activeIds.add(vehicle.vehicle_id)
    vehicleMissingFrames.delete(vehicle.vehicle_id)
    const existing = vehicleFeatures.get(vehicle.vehicle_id)
    const feature = existing?.feature ?? new Feature<Point>({ geometry: new Point(target) })
    feature.set('color', definition.color)
    feature.set('vtype', definition.type)
    const targetRotation = Math.PI / 2 - resolvedHeading.heading
    if (existing) {
      if (snapVehiclePositions) {
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
    if (!isNewSourceSnapshot && !snapVehiclePositions) continue
    const missingFrames = (vehicleMissingFrames.get(id) ?? 0) + 1
    vehicleMissingFrames.set(id, missingFrames)
    if (!snapVehiclePositions && missingFrames <= 4) continue
    vehicleMissingFrames.delete(id)
    vehicleHeadingHistory.delete(id)
    const state = vehicleFeatures.get(id)
    if (state) vehicleSource.removeFeature(state.feature)
    vehicleFeatures.delete(id)
  }
  snapVehiclePositions = false
  scheduleVehicleAnimation()
}

function focusActiveIntersection(): void {
  const node = topologyNodes.find((item) => item.intersectionId === activeIntersectionId.value)
  if (!node) return
  mapView.focusIntersection(
    [node.longitude, node.latitude],
    node.intersectionId,
    { force: true, duration: 700 },
  )
}

onMounted(() => {
  if (!mapEl.value) {
    return
  }

  map = new Map({
    target: mapEl.value,
    layers: [
      createBasemapLayer(DEFAULT_APP_BASEMAP),
      networkLayer,
      vehicleLayer,
      disturbanceLayer,
      detectedLayer,
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
      positioning: 'bottom-left',
      offset: [16, -18],
      stopEvent: false,
    })
    map.addOverlay(detectedOverlay)
  }
  map.on('singleclick', handleMapClick)
  map.on('pointermove', handleDetectedPointerMove)
  map.on('moveend', renderVehicles)
  renderNetwork()
  renderVehicles()
  renderDetectedEvents()
  syncEdgeCongestion()
  void loadIntersectionTopologyCatalog().then((nodes) => {
    topologyNodes = nodes
    focusActiveIntersection()
    renderDisturbanceWarnings()
  }).catch((cause: unknown) => console.warn('[disturbance-warning] topology unavailable', cause))

  resizeObserver = new ResizeObserver(() => {
    map?.updateSize()
  })
  resizeObserver.observe(mapEl.value)
})

watch(geojson, renderNetwork)
watch(trafficView, renderVehicles)
watch(renderSessionRevision, clearVehiclePresentation, { flush: 'sync' })
watch(() => props.active, (active) => {
  if (active) {
    map?.updateSize()
    snapVehiclePositions = true
    renderVehicles()
    map?.renderSync()
  } else if (vehicleAnimationFrame !== null) {
    cancelAnimationFrame(vehicleAnimationFrame)
    vehicleAnimationFrame = null
  }
})
watch(activeIntersectionId, focusActiveIntersection)
watch([disturbanceEvents, snapshot, simulationStartTime], renderDisturbanceWarnings, { deep: true })
watch(snapshot, () => {
  renderDetectedEvents()
  syncEdgeCongestion()
})

function handleDetectedPointerMove(event: MapBrowserEvent): void {
  if (!map || !detectedOverlay) return
  if (event.dragging) return
  const feature = map.forEachFeatureAtPixel(event.pixel, (candidate, layer) => (
    layer === detectedLayer ? candidate : undefined
  ))
  if (!feature) {
    detectedOverlay.setPosition(undefined)
    return
  }
  warningOverlay?.setPosition(undefined)
  detectedPopupClock.value = String(feature.get('clock') ?? '')
  detectedPopupType.value = String(feature.get('typeLabel') ?? '')
  detectedPopupFlow.value = String(feature.get('flowSummary') ?? '')
  detectedOverlay.setPosition((feature.getGeometry() as Point).getCoordinates())
}

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  map?.un('singleclick', handleMapClick)
  map?.un('pointermove', handleDetectedPointerMove)
  map?.un('moveend', renderVehicles)
  warningOverlay?.setPosition(undefined)
  warningOverlay = null
  detectedOverlay?.setPosition(undefined)
  detectedOverlay = null
  topologyNodes = []
  clearVehiclePresentation()
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="app-background-map">
    <div ref="mapEl" class="app-background-map__canvas" />
    <div v-if="networkError" class="app-background-map__network-status">
      当前路口路网加载失败，已保留深色底图定位
    </div>
    <div ref="warningPopupRef" class="disturbance-warning-popup">
      <strong>{{ warningPopupTitle }}</strong>
      <div v-for="event in warningPopupEvents" :key="event.id">
        <span>{{ event.label }}</span>
        <time>{{ event.time }}</time>
      </div>
    </div>
    <div ref="detectedPopupRef" class="detected-event-popup">
      <strong>事件识别</strong>
      <div><span>事件检测时间</span><time>{{ detectedPopupClock }}</time></div>
      <div><span>事件类型</span><b>{{ detectedPopupType }}</b></div>
      <div class="detected-event-popup__flow"><span>预计未来车流</span><b>{{ detectedPopupFlow }}</b></div>
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

.detected-event-popup {
  min-width: 220px;
  max-width: 300px;
  display: grid;
  gap: 7px;
  padding: 10px 12px;
  border: 1px solid rgba(82, 194, 250, 0.55);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(8, 42, 68, 0.96), rgba(2, 16, 31, 0.96));
  box-shadow: 0 8px 24px rgba(0, 20, 40, 0.45), 0 0 18px rgba(33, 230, 255, 0.18);
  color: #e8f8ff;
  font: 12px/1.45 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.detected-event-popup:empty { display: none; }
.detected-event-popup strong { color: #52c2fa; font-size: 13px; }
.detected-event-popup div { display: grid; grid-template-columns: 88px 1fr; gap: 8px; }
.detected-event-popup span { color: #8fd8ff; }
.detected-event-popup b,
.detected-event-popup time { color: #f4fcff; font-weight: 600; }
.detected-event-popup__flow { align-items: start; }
</style>
