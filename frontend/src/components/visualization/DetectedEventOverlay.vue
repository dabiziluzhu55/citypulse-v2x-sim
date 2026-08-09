<script setup lang="ts">
// 算法识别事件图标与蓝色Hover卡片覆盖层与扰动无关
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { DetectedEventCard } from '../../types/intelligence'
import type { SimulationSnapshot } from '../../types/simulation'
import {
  DETECTED_EVENT_ICON_URL,
  activeDetectedEventCards,
  detectedEventClockTime,
  detectedEventFlowSummary,
  detectedEventTypeLabel,
} from '../../utils/detectedEventDisplay'

export interface ScreenPoint {
  x: number
  y: number
}

const props = defineProps<{
  cards: DetectedEventCard[]
  snapshot: SimulationSnapshot | null
  project: (longitude: number, latitude: number) => ScreenPoint | null
  active?: boolean
}>()

interface MarkerView {
  card: DetectedEventCard
  x: number
  y: number
  clock: string
  typeLabel: string
  flowSummary: string
}

const markers = ref<MarkerView[]>([])
const hoveredId = ref<string | null>(null)
let frameId: number | null = null

const activeCards = computed(() => activeDetectedEventCards(props.cards))

const hoveredMarker = computed(() => (
  markers.value.find((item) => item.card.event_id === hoveredId.value) ?? null
))

function refreshMarkers(): void {
  if (!props.active) {
    markers.value = []
    hoveredId.value = null
    return
  }
  const next: MarkerView[] = []
  for (const card of activeCards.value) {
    const point = props.project(Number(card.longitude), Number(card.latitude))
    if (!point) continue
    next.push({
      card,
      x: point.x,
      y: point.y,
      clock: detectedEventClockTime(props.snapshot, card.start_seconds),
      typeLabel: detectedEventTypeLabel(card),
      flowSummary: detectedEventFlowSummary(card),
    })
  }
  markers.value = next
  if (hoveredId.value && !next.some((item) => item.card.event_id === hoveredId.value)) {
    hoveredId.value = null
  }
}

function loop(): void {
  refreshMarkers()
  frameId = window.requestAnimationFrame(loop)
}

watch(
  () => [props.cards, props.snapshot?.sequence, props.active] as const,
  () => refreshMarkers(),
  { deep: true },
)

onMounted(() => {
  refreshMarkers()
  frameId = window.requestAnimationFrame(loop)
})

onUnmounted(() => {
  if (frameId != null) window.cancelAnimationFrame(frameId)
})
</script>

<template>
  <div class="detected-event-overlay" aria-label="算法识别事件">
    <button
      v-for="marker in markers"
      :key="marker.card.event_id"
      type="button"
      class="detected-event-overlay__marker"
      :style="{ left: `${marker.x}px`, top: `${marker.y}px` }"
      :aria-label="`识别事件 ${marker.typeLabel}`"
      @mouseenter="hoveredId = marker.card.event_id"
      @mouseleave="hoveredId = null"
      @focus="hoveredId = marker.card.event_id"
      @blur="hoveredId = null"
    >
      <img
        class="detected-event-overlay__icon"
        :src="DETECTED_EVENT_ICON_URL"
        alt=""
        draggable="false"
      >
    </button>

    <div
      v-if="hoveredMarker"
      class="detected-event-overlay__card"
      :style="{ left: `${hoveredMarker.x}px`, top: `${hoveredMarker.y}px` }"
      role="tooltip"
    >
      <div class="detected-event-overlay__card-title">事件识别</div>
      <div class="detected-event-overlay__row">
        <span>事件检测时间</span>
        <strong>{{ hoveredMarker.clock }}</strong>
      </div>
      <div class="detected-event-overlay__row">
        <span>事件类型</span>
        <strong>{{ hoveredMarker.typeLabel }}</strong>
      </div>
      <div class="detected-event-overlay__row detected-event-overlay__row--flow">
        <span>预计未来车流</span>
        <strong>{{ hoveredMarker.flowSummary }}</strong>
      </div>
    </div>
  </div>
</template>

<style scoped>
.detected-event-overlay {
  position: absolute;
  inset: 0;
  z-index: 6;
  pointer-events: none;
  overflow: hidden;
}

.detected-event-overlay__marker {
  position: absolute;
  width: 34px;
  height: 34px;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  transform: translate(-50%, -88%);
  pointer-events: auto;
  cursor: pointer;
  filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.45));
}

.detected-event-overlay__icon {
  width: 34px;
  height: 34px;
  display: block;
  user-select: none;
}

.detected-event-overlay__card {
  position: absolute;
  z-index: 7;
  min-width: 220px;
  max-width: 300px;
  padding: 10px 12px;
  border: 1px solid rgba(82, 194, 250, 0.55);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(8, 42, 68, 0.96), rgba(2, 16, 31, 0.96));
  box-shadow: 0 8px 24px rgba(0, 20, 40, 0.45), 0 0 18px rgba(33, 230, 255, 0.18);
  color: #e8f8ff;
  transform: translate(18px, -110%);
  pointer-events: none;
}

.detected-event-overlay__card-title {
  margin-bottom: 8px;
  color: #52c2fa;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.detected-event-overlay__row {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 8px;
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.45;
}

.detected-event-overlay__row span {
  color: #8fd8ff;
}

.detected-event-overlay__row strong {
  color: #f4fcff;
  font-weight: 600;
}

.detected-event-overlay__row--flow {
  align-items: start;
}
</style>
