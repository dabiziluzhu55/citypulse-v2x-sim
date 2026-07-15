<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import { defaults as defaultControls, Attribution } from 'ol/control'
import { defaults as defaultInteractions } from 'ol/interaction'
import { fromLonLat } from 'ol/proj'
import 'ol/ol.css'
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../constants/mapDefaults'
import {
  createBasemapLayer,
  DEFAULT_PANEL_BASEMAP,
  type BasemapVariant,
} from '../../constants/mapBasemaps'

const props = withDefaults(
  defineProps<{
    center?: [number, number]
    zoom?: number
    variant?: BasemapVariant
    interactive?: boolean
    showAttribution?: boolean
  }>(),
  {
    center: () => DEFAULT_MAP_CENTER,
    zoom: DEFAULT_MAP_ZOOM,
    variant: DEFAULT_PANEL_BASEMAP,
    interactive: true,
    showAttribution: true,
  },
)

const mapEl = ref<HTMLElement | null>(null)
let map: Map | null = null
let resizeObserver: ResizeObserver | null = null

function syncView() {
  if (!map) {
    return
  }
  const view = map.getView()
  view.setCenter(fromLonLat(props.center))
  view.setZoom(props.zoom)
}

function initMap() {
  if (!mapEl.value || map) {
    return
  }

  map = new Map({
    target: mapEl.value,
    layers: [createBasemapLayer(props.variant)],
    view: new View({
      center: fromLonLat(props.center),
      zoom: props.zoom,
    }),
    controls: props.showAttribution
      ? defaultControls({
          attribution: false,
          zoom: false,
          rotate: false,
        }).extend([
          new Attribution({
            collapsible: false,
          }),
        ])
      : [],
    interactions: props.interactive ? defaultInteractions() : [],
  })

  resizeObserver = new ResizeObserver(() => {
    map?.updateSize()
  })
  resizeObserver.observe(mapEl.value)
}

watch(
  () => [props.center[0], props.center[1], props.zoom] as const,
  () => {
    syncView()
  },
)

onMounted(() => {
  initMap()
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div ref="mapEl" class="ol-map-host" :class="{ 'is-static': !interactive }" />
</template>

<style scoped>
.ol-map-host {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
}

.ol-map-host.is-static {
  pointer-events: none;
}

.ol-map-host :deep(.ol-viewport) {
  border-radius: inherit;
}

.ol-map-host :deep(.ol-attribution) {
  right: auto;
  left: 8px;
  bottom: 88px;
  max-width: calc(100% - 32px);
  border-radius: 6px;
  background: rgba(1, 14, 26, 0.78);
  color: #78aeca;
  font-size: 11px;
  padding: 4px 8px;
}

.ol-map-host :deep(.ol-attribution a) {
  color: #21e6ff;
}
</style>
