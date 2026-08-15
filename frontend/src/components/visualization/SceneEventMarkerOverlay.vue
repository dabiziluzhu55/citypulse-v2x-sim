<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { SimulationSnapshot } from '../../types/simulation'
import type { SceneEventDetail, SceneEventMarker } from '../../mapv/sceneEventMarkers'
import { EVENT_MARKER_HIT_SIZE_PIXELS } from '../../mapv/sceneEventMarkers'
import {
  detectedEventClockTime,
  detectedEventDurationSeconds,
  detectedEventFlowSummary,
  detectedEventTypeLabel,
  formatDetectedEventDuration,
} from '../../utils/detectedEventDisplay'
import {
  disturbanceRuntimeStateLabel,
  disturbanceRuntimeTypeLabel,
} from '../../utils/runtimeDisturbances'

interface ScreenPoint { x: number; y: number }
interface ProjectedMarker extends SceneEventMarker, ScreenPoint {}
interface CardPosition { left: number; top: number; placement: 'above' | 'below' }

const props = defineProps<{
  markers: SceneEventMarker[]
  snapshot: SimulationSnapshot | null
  project: (marker: SceneEventMarker) => ScreenPoint | null
  active?: boolean
  continuous?: boolean
  viewToken?: string | number
  sessionRevision?: number
}>()

const projected = ref<ProjectedMarker[]>([])
const selectedId = ref<string | null>(null)
const selectedDetailIndex = ref(0)
let frameId: number | null = null

const selected = computed(() => projected.value.find((marker) => marker.id === selectedId.value) ?? null)
const selectedDetail = computed(() => selected.value?.details[selectedDetailIndex.value] ?? null)
const cardPosition = computed<CardPosition | null>(() => {
  const marker = selected.value
  if (!marker) return null
  const viewportWidth = typeof window === 'undefined' ? 1280 : window.innerWidth
  const viewportHeight = typeof window === 'undefined' ? 720 : window.innerHeight
  const width = Math.min(340, viewportWidth - 32)
  const estimatedHeight = Math.min(440, viewportHeight - 32)
  const left = Math.max(16, Math.min(viewportWidth - width - 16, marker.x + 20))
  const placement = marker.y - 16 >= estimatedHeight ? 'above' : 'below'
  const top = placement === 'above'
    ? marker.y - estimatedHeight - 12
    : marker.y + 16
  return {
    left,
    top: Math.max(16, Math.min(viewportHeight - estimatedHeight - 16, top)),
    placement,
  }
})

function refresh(): void {
  if (!props.active) {
    projected.value = []
    selectedId.value = null
    return
  }
  projected.value = props.markers.flatMap((marker) => {
    const point = props.project(marker)
    return point ? [{ ...marker, ...point }] : []
  })
  if (selectedId.value && !projected.value.some((marker) => marker.id === selectedId.value)) {
    selectedId.value = null
  }
}

function syncLoop(): void {
  const shouldRun = Boolean(props.active && props.continuous && props.markers.length)
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

function toggle(marker: ProjectedMarker): void {
  if (selectedId.value === marker.id) {
    selectedId.value = null
    return
  }
  selectedId.value = marker.id
  selectedDetailIndex.value = 0
}

function close(): void {
  selectedId.value = null
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || !selectedId.value) return
  event.stopPropagation()
  close()
}

function onWindowPointerDown(): void {
  if (selectedId.value) close()
}

function detailTitle(detail: SceneEventDetail): string {
  return detail.kind === 'detected'
    ? detectedEventTypeLabel(detail.card)
    : disturbanceRuntimeTypeLabel(detail.event.eventType)
}

function detailRows(detail: SceneEventDetail): Array<[string, string]> {
  if (detail.kind === 'detected') {
    const card = detail.card
    return [
      ['识别类型', detailTitle(detail)],
      ['发生时间', detectedEventClockTime(props.snapshot, card.start_seconds)],
      ['持续时间', formatDetectedEventDuration(detectedEventDurationSeconds(props.snapshot, card))],
      ['路口', card.intersection_id],
      ['车道', card.lane_ids.join('、') || '--'],
      ['严重程度', card.severity || '--'],
      ['置信度', Number.isFinite(card.confidence) ? `${Math.round(card.confidence * 100)}%` : '--'],
      ['原因', card.cause || '--'],
      ['证据', card.evidence.join('；') || '--'],
      ['处置建议', card.suggestion || '--'],
      ['短时预测', detectedEventFlowSummary(card)],
    ]
  }
  const event = detail.event
  const lanes = [event.details.lane_id, event.details.venue_lane_id, event.details.lane_ids]
    .flatMap((value) => Array.isArray(value) ? value : value ? [value] : [])
    .filter((value): value is string => typeof value === 'string')
  const position = Number(event.details.position_ratio)
  const vehicleCount = Number(event.details.vehicle_count)
  return [
    ['事件类型', detailTitle(detail)],
    ['运行状态', disturbanceRuntimeStateLabel(event.state)],
    ['路口', event.intersectionId],
    ['起止时间', `${event.startSeconds}s - ${event.endSeconds}s`],
    ['影响车道', lanes.join('、') || '--'],
    ...(event.eventType === 'accident' && Number.isFinite(position)
      ? [['事故位置', `车道全长的 ${Math.round(position * 100)}%`] as [string, string]]
      : []),
    ...(event.eventType.startsWith('major_event_') && Number.isFinite(vehicleCount)
      ? [['活动车辆', `${Math.round(vehicleCount)} 辆`] as [string, string]]
      : []),
  ]
}

watch(
  () => [props.markers, props.snapshot?.sequence, props.active, props.continuous, props.viewToken] as const,
  () => { refresh(); syncLoop() },
  { deep: true },
)

watch(
  () => props.sessionRevision,
  () => {
    selectedId.value = null
    selectedDetailIndex.value = 0
    projected.value = []
  },
  { flush: 'sync' },
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown, true)
  window.addEventListener('pointerdown', onWindowPointerDown)
  refresh()
  syncLoop()
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown, true)
  window.removeEventListener('pointerdown', onWindowPointerDown)
  if (frameId != null) cancelAnimationFrame(frameId)
})
</script>

<template>
  <div class="scene-event-overlay" aria-label="三维事件标识">
    <button
      v-for="marker in projected"
      :key="marker.id"
      type="button"
      class="scene-event-overlay__hit"
      :class="`is-${marker.color}`"
      :style="{
        left: `${marker.x}px`,
        top: `${marker.y}px`,
        width: `${EVENT_MARKER_HIT_SIZE_PIXELS}px`,
        height: `${EVENT_MARKER_HIT_SIZE_PIXELS}px`,
      }"
      :aria-label="`${marker.color === 'red' ? '红色' : '黄色'}事件标识，共${marker.details.length}个事件`"
      :aria-expanded="selectedId === marker.id"
      @pointerdown.stop
      @click.stop="toggle(marker)"
    />

    <section
      v-if="selected && selectedDetail"
      class="scene-event-overlay__card"
      :class="`is-${cardPosition?.placement ?? 'above'}`"
      :style="{ left: `${cardPosition?.left ?? selected.x}px`, top: `${cardPosition?.top ?? selected.y}px` }"
      role="dialog"
      aria-modal="false"
      aria-label="事件详情"
      @pointerdown.stop
      @click.stop
    >
      <header>
        <div>
          <span>{{ selected.color === 'red' ? '重点事件' : '事件识别' }}</span>
          <strong>{{ detailTitle(selectedDetail) }}</strong>
        </div>
        <button type="button" title="关闭" aria-label="关闭事件详情" @click="close">×</button>
      </header>
      <nav v-if="selected.details.length > 1" aria-label="关联事件">
        <button
          v-for="(detail, index) in selected.details"
          :key="detail.id"
          type="button"
          :class="{ 'is-active': index === selectedDetailIndex }"
          @click="selectedDetailIndex = index"
        >
          {{ detailTitle(detail) }}
        </button>
      </nav>
      <dl>
        <template v-for="row in detailRows(selectedDetail)" :key="row[0]">
          <dt>{{ row[0] }}</dt>
          <dd>{{ row[1] }}</dd>
        </template>
      </dl>
    </section>
  </div>
</template>

<style scoped>
.scene-event-overlay { position: absolute; inset: 0; z-index: 9; overflow: hidden; pointer-events: none; }
.scene-event-overlay__hit { position: absolute; padding: 0; border: 0; background: transparent; transform: translate(-50%, -100%); pointer-events: auto; cursor: pointer; }
.scene-event-overlay__hit:focus-visible { outline: 2px solid #fff; outline-offset: 2px; border-radius: 50%; }
.scene-event-overlay__card { position: absolute; z-index: 10; width: min(340px, calc(100vw - 32px)); max-height: min(440px, calc(100vh - 32px)); overflow: auto; padding: 12px; border: 1px solid rgba(82, 194, 250, .55); border-radius: 6px; background: rgba(3, 20, 31, .97); box-shadow: 0 10px 28px rgba(0, 0, 0, .55); color: #eaf8ff; pointer-events: auto; }
.scene-event-overlay__card header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.scene-event-overlay__card header div { display: grid; gap: 3px; }
.scene-event-overlay__card header span { color: #7fdcff; font-size: 11px; }
.scene-event-overlay__card header strong { font-size: 14px; }
.scene-event-overlay__card header button { width: 26px; height: 26px; padding: 0; border: 0; background: transparent; color: #dff7ff; font-size: 20px; cursor: pointer; }
.scene-event-overlay__card nav { display: flex; gap: 6px; margin: 10px 0; overflow-x: auto; }
.scene-event-overlay__card nav button { flex: 0 0 auto; padding: 4px 8px; border: 1px solid rgba(130, 205, 236, .35); border-radius: 4px; background: rgba(16, 61, 78, .55); color: #a8d9ea; font-size: 11px; cursor: pointer; }
.scene-event-overlay__card nav button.is-active { border-color: #4cdbff; color: #fff; }
.scene-event-overlay__card dl { display: grid; grid-template-columns: 78px minmax(0, 1fr); gap: 7px 10px; margin: 12px 0 0; font-size: 12px; line-height: 1.45; }
.scene-event-overlay__card dt { color: #86cce5; }
.scene-event-overlay__card dd { min-width: 0; margin: 0; overflow-wrap: anywhere; color: #f2fbff; }
</style>
