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
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style, Text } from 'ol/style'
import { defaults as defaultControls, Attribution } from 'ol/control'
import { defaults as defaultInteractions } from 'ol/interaction'
import { fromLonLat } from 'ol/proj'
import 'ol/ol.css'
import { createBasemapLayer, DEFAULT_APP_BASEMAP } from '../../constants/mapBasemaps'
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../constants/mapDefaults'
import { bindMapInstance, useAppMapView } from '../../composables/useAppMapView'
import { useSimulationMap } from '../../composables/useSimulationMap'
import { useSimulationStore } from '../../composables/useSimulationStore'
import { TrafficModelRegistry } from '../../cesium/traffic/TrafficModelRegistry'
import { loadIntersectionTopologyCatalog, type IntersectionTopologyNode } from '../../mapv/intersectionTopology'
import { DISTURBANCE_EVENT_OPTIONS } from '../../constants/scenarioOptions'
import { useScenarioDraftStore, type ScenarioDraftDisturbanceEvent } from '../../composables/useScenarioDraftStore'
import { formatIntersectionLabel } from '../../utils/intersectionLabels'
import { buildDisturbanceWarningAggregates } from '../../utils/disturbanceWarnings'

const mapEl = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const { geojson } = useSimulationMap()
const { trafficView, snapshot } = useSimulationStore()
const { disturbanceEvents, simulationStartTime } = useScenarioDraftStore()

const warningPopupRef = ref<HTMLElement | null>(null)
const warningPopupTitle = ref('')
const warningPopupEvents = ref<Array<{ id: string; label: string; time: string }>>([])

let map: Map | null = null
let resizeObserver: ResizeObserver | null = null
let warningOverlay: Overlay | null = null
let topologyNodes: IntersectionTopologyNode[] = []

const networkSource = new VectorSource()
const vehicleSource = new VectorSource()
const disturbanceSource = new VectorSource()
const geoJsonFormat = new GeoJSON()
const modelRegistry = new TrafficModelRegistry()

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
    return new Style({
      stroke: new Stroke({ color: 'rgba(90, 180, 255, 0.55)', width: 2 }),
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

function handleMapClick(event: MapBrowserEvent): void {
  if (!map || !warningOverlay) return
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

function renderVehicles() {
  vehicleSource.clear()
  const vehicles = trafficView.value?.vehicles ?? []
  const features: Feature[] = []
  for (const vehicle of vehicles) {
    if (vehicle.longitude == null || vehicle.latitude == null) {
      continue
    }
    const definition = modelRegistry.resolve(vehicle.vehicle_id, vehicle.lane_id)
    const feature = new Feature({
      geometry: new Point(fromLonLat([vehicle.longitude, vehicle.latitude])),
    })
    feature.set('color', definition.color)
    feature.set('vtype', definition.type)
    // SUMO angle：正北为 0、顺时针为正（度）；三角形默认顶点朝上（北），
    // OpenLayers rotation 为弧度、顺时针为正，可直接换算。
    feature.set('rotation', (vehicle.angle * Math.PI) / 180)
    features.push(feature)
  }
  vehicleSource.addFeatures(features)
}

onMounted(() => {
  if (!mapEl.value) {
    return
  }

  map = new Map({
    target: mapEl.value,
    layers: [createBasemapLayer(DEFAULT_APP_BASEMAP), networkLayer, vehicleLayer, disturbanceLayer],
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
  renderNetwork()
  renderVehicles()
  void loadIntersectionTopologyCatalog().then((nodes) => {
    topologyNodes = nodes
    renderDisturbanceWarnings()
  }).catch((cause: unknown) => console.warn('[disturbance-warning] topology unavailable', cause))

  resizeObserver = new ResizeObserver(() => {
    map?.updateSize()
  })
  resizeObserver.observe(mapEl.value)
})

watch(geojson, renderNetwork)
watch(trafficView, renderVehicles, { deep: true })
watch([disturbanceEvents, snapshot, simulationStartTime], renderDisturbanceWarnings, { deep: true })

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  map?.un('singleclick', handleMapClick)
  warningOverlay?.setPosition(undefined)
  warningOverlay = null
  topologyNodes = []
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="app-background-map">
    <div ref="mapEl" class="app-background-map__canvas" />
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
