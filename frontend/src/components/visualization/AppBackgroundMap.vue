<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import GeoJSON from 'ol/format/GeoJSON'
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style } from 'ol/style'
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

const mapEl = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
const { geojson } = useSimulationMap()
const { trafficView } = useSimulationStore()

let map: Map | null = null
let resizeObserver: ResizeObserver | null = null

const networkSource = new VectorSource()
const vehicleSource = new VectorSource()
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
    layers: [createBasemapLayer(DEFAULT_APP_BASEMAP), networkLayer, vehicleLayer],
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
  renderNetwork()
  renderVehicles()

  resizeObserver = new ResizeObserver(() => {
    map?.updateSize()
  })
  resizeObserver.observe(mapEl.value)
})

watch(geojson, renderNetwork)
watch(trafficView, renderVehicles, { deep: true })

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="app-background-map">
    <div ref="mapEl" class="app-background-map__canvas" />
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
</style>
