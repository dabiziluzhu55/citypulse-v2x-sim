<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import type { SimulationEvent } from '../../types/simulation'
import {
  disturbanceRuntimeStateLabel,
  disturbanceRuntimeTypeLabel,
  type DisturbanceRuntimeView,
} from '../../utils/runtimeDisturbances'

interface PositionedRuntimeDisturbance extends DisturbanceRuntimeView {
  longitude: number
  latitude: number
}

interface ScreenPoint {
  x: number
  y: number
}

const props = defineProps<{
  events: PositionedRuntimeDisturbance[]
  unmappedEvents?: SimulationEvent[]
  project: (longitude: number, latitude: number) => ScreenPoint | null
  active?: boolean
  continuous?: boolean
  viewToken?: string | number
}>()

const markers = ref<Array<PositionedRuntimeDisturbance & ScreenPoint>>([])
const hoveredId = ref<string | null>(null)
let frameId: number | null = null

const hovered = computed(() => (
  markers.value.find((marker) => marker.eventId === hoveredId.value) ?? null
))

function refresh(): void {
  if (!props.active) {
    markers.value = []
    hoveredId.value = null
    return
  }
  markers.value = props.events.flatMap((event) => {
    const point = props.project(event.longitude, event.latitude)
    return point ? [{ ...event, ...point }] : []
  })
  if (hoveredId.value && !markers.value.some((marker) => marker.eventId === hoveredId.value)) {
    hoveredId.value = null
  }
}

function syncLoop(): void {
  const shouldRun = props.active && props.continuous && props.events.length > 0
  if (!shouldRun && frameId != null) {
    cancelAnimationFrame(frameId)
    frameId = null
  }
  if (shouldRun && frameId == null) {
    frameId = requestAnimationFrame(() => {
      frameId = null
      refresh()
      syncLoop()
    })
  }
}

function parameterSummary(event: DisturbanceRuntimeView): string {
  const maxSpeed = Number(event.details.max_speed)
  if (event.eventType === 'speed_limit' && Number.isFinite(maxSpeed)) {
    return `限速 ${Math.round(maxSpeed * 3.6)} km/h`
  }
  const vehicleCount = Number(event.details.vehicle_count)
  if (event.eventType.startsWith('major_event_') && Number.isFinite(vehicleCount)) {
    return `影响车辆 ${Math.round(vehicleCount)} 辆`
  }
  return `${event.startSeconds}s-${event.endSeconds}s`
}

watch(
  () => [props.events, props.unmappedEvents, props.active, props.continuous, props.viewToken] as const,
  () => {
    refresh()
    syncLoop()
  },
  { deep: true, immediate: true },
)

onUnmounted(() => {
  if (frameId != null) cancelAnimationFrame(frameId)
  frameId = null
})
</script>

<template>
  <div class="runtime-disturbance-overlay" aria-label="用户扰动运行状态">
    <button
      v-for="marker in markers"
      :key="marker.eventId"
      type="button"
      class="runtime-disturbance-overlay__marker"
      :class="[`is-${marker.state.toLowerCase()}`, `is-${marker.eventType}`]"
      :style="{ left: `${marker.x}px`, top: `${marker.y}px` }"
      :aria-label="`${disturbanceRuntimeTypeLabel(marker.eventType)} ${disturbanceRuntimeStateLabel(marker.state)}`"
      @mouseenter="hoveredId = marker.eventId"
      @mouseleave="hoveredId = null"
      @focus="hoveredId = marker.eventId"
      @blur="hoveredId = null"
    >
      <span aria-hidden="true">!</span>
    </button>

    <div
      v-if="hovered"
      class="runtime-disturbance-overlay__card"
      :style="{ left: `${hovered.x}px`, top: `${hovered.y}px` }"
      role="tooltip"
    >
      <strong>{{ disturbanceRuntimeTypeLabel(hovered.eventType) }}</strong>
      <span>{{ disturbanceRuntimeStateLabel(hovered.state) }}</span>
      <span>{{ parameterSummary(hovered) }}</span>
      <span v-if="hovered.error" class="is-error">{{ hovered.error }}</span>
    </div>

    <div v-if="active && unmappedEvents?.length" class="runtime-disturbance-overlay__unmapped">
      {{ unmappedEvents.length }} 个扰动事件的位置无法从旧会话恢复
    </div>
  </div>
</template>

<style scoped>
.runtime-disturbance-overlay {
  position: absolute;
  inset: 0;
  z-index: 8;
  overflow: hidden;
  pointer-events: none;
}

.runtime-disturbance-overlay__marker {
  position: absolute;
  width: 32px;
  height: 32px;
  margin: 0;
  padding: 0;
  border: 2px solid #fff;
  border-radius: 50%;
  background: #d9152f;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
  color: #fff;
  font: 800 18px/28px sans-serif;
  transform: translate(-50%, -85%);
  cursor: pointer;
  pointer-events: auto;
}

.runtime-disturbance-overlay__marker.is-scheduled {
  border-color: #ffbd59;
  background: rgba(7, 22, 31, 0.88);
  color: #ffbd59;
}

.runtime-disturbance-overlay__marker.is-speed_limit.is-active {
  background: #ff8a00;
}

.runtime-disturbance-overlay__marker.is-completed,
.runtime-disturbance-overlay__marker.is-cancelled {
  border-color: #b5c0c5;
  background: #66777f;
  opacity: 0.68;
}

.runtime-disturbance-overlay__marker.is-failed {
  background: #b80f2b;
  box-shadow: 0 0 0 4px rgba(255, 36, 63, 0.28);
}

.runtime-disturbance-overlay__card {
  position: absolute;
  display: grid;
  gap: 5px;
  min-width: 180px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 183, 77, 0.72);
  border-radius: 6px;
  background: rgba(4, 20, 29, 0.96);
  color: #d9edf5;
  font-size: 12px;
  transform: translate(18px, -105%);
}

.runtime-disturbance-overlay__card strong {
  color: #fff;
  font-size: 13px;
}

.runtime-disturbance-overlay__card .is-error {
  color: #ff8797;
}

.runtime-disturbance-overlay__unmapped {
  position: absolute;
  right: 24px;
  bottom: 62px;
  max-width: 320px;
  padding: 7px 10px;
  border: 1px solid rgba(255, 183, 77, 0.55);
  border-radius: 6px;
  background: rgba(4, 20, 29, 0.9);
  color: #ffd28c;
  font-size: 12px;
}
</style>
