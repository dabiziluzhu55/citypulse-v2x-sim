<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import { defaults as defaultControls, Attribution } from 'ol/control'
import { defaults as defaultInteractions } from 'ol/interaction'
import { fromLonLat } from 'ol/proj'
import 'ol/ol.css'
import { createBasemapLayer, DEFAULT_APP_BASEMAP } from '../../constants/mapBasemaps'
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../constants/mapDefaults'
import { bindMapInstance, useAppMapView } from '../../composables/useAppMapView'

const mapEl = ref<HTMLElement | null>(null)
const mapView = useAppMapView()
let map: Map | null = null
let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  if (!mapEl.value) {
    return
  }

  map = new Map({
    target: mapEl.value,
    layers: [createBasemapLayer(DEFAULT_APP_BASEMAP)],
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

  resizeObserver = new ResizeObserver(() => {
    map?.updateSize()
  })
  resizeObserver.observe(mapEl.value)
})

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
