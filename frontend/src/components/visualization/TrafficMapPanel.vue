<script setup lang="ts">
import { computed } from 'vue'
import type { RunOverview } from '../../types/overview'
import type { RunStatus } from '../../types/simulation'
import type { TrafficStateSnapshot, TrafficSummary } from '../../types/traffic'
import { createMapTransform } from '../../utils/coordinateTransform'
import { formatNumber, formatSimTime } from '../../utils/format'
import {
  resolveTrafficStatusColor,
  resolveVehicleColor,
} from '../../constants/trafficVisualization'

const props = defineProps<{
  trafficState: TrafficStateSnapshot | null
  summary: TrafficSummary
  overview: RunOverview | null
  runStatus: RunStatus | null
  runId: string
  loading: boolean
  error: string | null
  wsConnected: boolean
}>()

const mapPoints = computed(() => {
  const state = props.trafficState
  if (!state) {
    return []
  }
  return [
    ...state.intersections.map((item) => ({ x: item.x, y: item.y })),
    ...state.vehicles.map((item) => ({ x: item.x, y: item.y })),
  ]
})

const mapTransform = computed(() => createMapTransform(mapPoints.value))

const intersections = computed(() => {
  if (!props.trafficState) {
    return []
  }

  return props.trafficState.intersections.map((item) => {
    const point = mapTransform.value.toScreen({ x: item.x, y: item.y })
    return { ...item, sx: point.x, sy: point.y }
  })
})

const vehicles = computed(() => {
  if (!props.trafficState) {
    return []
  }

  return props.trafficState.vehicles.map((item) => {
    const point = mapTransform.value.toScreen({ x: item.x, y: item.y })
    return {
      ...item,
      sx: point.x,
      sy: point.y,
      color: resolveVehicleColor(item.waiting_time, item.speed),
    }
  })
})

const scenarioBadge = computed(
  () => props.overview?.scenario_name ?? (props.runId ? '仿真运行中' : '等待启动仿真'),
)

const overlayMetrics = computed(() => ({
  vehicleCount:
    props.summary.vehicle_count ??
    props.overview?.vehicle_count ??
    props.runStatus?.vehicle_count ??
    '--',
  avgSpeed:
    props.summary.avg_speed ?? props.overview?.avg_speed ?? null,
  waitTime: props.overview?.avg_waiting_time ?? null,
  queueLen: props.overview?.avg_queue_length ?? null,
}))

const timelineProgress = computed(() => {
  const simTime = props.trafficState?.sim_time ?? props.runStatus?.sim_time ?? 0
  const duration = 3600
  return Math.min(100, (simTime / duration) * 100)
})

const timelineLabel = computed(() => {
  const simTime = props.trafficState?.sim_time ?? props.runStatus?.sim_time ?? 0
  return `当前：${formatSimTime(simTime)} / 60:00`
})

function intersectionFill(status: string) {
  const color = resolveTrafficStatusColor(status)
  return `${color}24`
}

function intersectionStroke(status: string) {
  return resolveTrafficStatusColor(status)
}
</script>

<template>
  <section class="dashboard-panel map-panel">
    <div class="dashboard-panel-title">
      <h2>仿真路网实时可视化</h2>
      <span class="dashboard-badge">{{ scenarioBadge }}</span>
    </div>

    <div class="dashboard-panel-body flush">
      <div class="map-wrap">
        <div v-if="loading && !trafficState" class="map-loading">
          <el-skeleton animated :rows="3" />
        </div>

        <el-alert
          v-else-if="error && !trafficState"
          class="map-alert"
          :title="error"
          type="error"
          show-icon
          :closable="false"
        />

        <svg
          v-if="trafficState"
          class="map-svg traffic-overlay"
          :viewBox="mapTransform.viewBox"
          role="img"
          aria-label="仿真交通态势叠加层"
        >
          <g v-for="item in intersections" :key="item.intersection_id">
            <rect
              :x="item.sx - 36"
              :y="item.sy - 28"
              width="72"
              height="56"
              rx="6"
              class="intersection"
              :fill="intersectionFill(item.status)"
              :stroke="intersectionStroke(item.status)"
            />
            <rect
              :x="item.sx - 42"
              :y="item.sy - 52"
              width="118"
              height="22"
              rx="5"
              class="label-box"
            />
            <text :x="item.sx - 34" :y="item.sy - 37" class="svg-label">
              {{ item.name }} · {{ item.phase_name }}
            </text>
            <rect
              :x="item.sx - 42"
              :y="item.sy + 34"
              width="96"
              height="22"
              rx="5"
              class="label-box"
            />
            <text :x="item.sx - 34" :y="item.sy + 49" class="svg-label">
              排队 {{ item.queue_length }}
            </text>
            <g :transform="`translate(${item.sx - 40}, ${item.sy - 18})`">
              <rect width="34" height="15" class="signal" />
              <circle
                cx="9"
                cy="7.5"
                r="4"
                :class="item.current_phase === 0 ? 'signal-red' : 'signal-dim'"
              />
              <circle
                cx="18"
                cy="7.5"
                r="4"
                :class="item.current_phase === 1 ? 'signal-green' : 'signal-dim'"
              />
              <circle
                cx="27"
                cy="7.5"
                r="4"
                :class="item.current_phase === 2 ? 'signal-yellow' : 'signal-dim'"
              />
            </g>
          </g>

          <circle
            v-for="vehicle in vehicles"
            :key="vehicle.vehicle_id"
            class="vehicle"
            :class="{
              warning: vehicle.color === '#ffd05a',
              danger: vehicle.color === '#ff4d6d',
            }"
            :cx="vehicle.sx"
            :cy="vehicle.sy"
            :r="vehicle.color === '#ff4d6d' ? 6 : 5"
            :fill="vehicle.color"
          />
        </svg>

        <aside class="map-overlay">
          <h3>实时运行摘要</h3>
          <div class="overlay-grid">
            <div class="metric-mini">
              <div class="v">{{ overlayMetrics.vehicleCount }}</div>
              <div class="k">车辆总数</div>
            </div>
            <div class="metric-mini">
              <div class="v">
                {{
                  typeof overlayMetrics.avgSpeed === 'number'
                    ? formatNumber(overlayMetrics.avgSpeed)
                    : '--'
                }}
              </div>
              <div class="k">平均速度 m/s</div>
            </div>
            <div class="metric-mini">
              <div class="v">
                {{
                  overlayMetrics.waitTime != null ? formatNumber(overlayMetrics.waitTime) : '--'
                }}
              </div>
              <div class="k">平均等待 s</div>
            </div>
            <div class="metric-mini">
              <div class="v">
                {{
                  overlayMetrics.queueLen != null ? formatNumber(overlayMetrics.queueLen) : '--'
                }}
              </div>
              <div class="k">平均排队 辆</div>
            </div>
          </div>
        </aside>

        <div class="map-legend">
          <div class="legend-item"><span class="mini-dot cyan" />正常车辆</div>
          <div class="legend-item"><span class="mini-dot yellow" />缓行车辆</div>
          <div class="legend-item"><span class="mini-dot red" />异常停车</div>
          <div class="legend-item">
            <span class="mini-dot green" />{{ wsConnected ? 'WS 已连接' : 'WS 未连接' }}
          </div>
        </div>

        <div class="timeline">
          <div class="timeline-head">
            <span>仿真进度</span>
            <span>{{ timelineLabel }}</span>
          </div>
          <div class="timeline-track">
            <div class="timeline-fill" :style="{ width: `${timelineProgress}%` }" />
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.map-panel {
  min-height: 620px;
  pointer-events: none;
}

.map-panel :deep(.dashboard-panel-title),
.map-panel :deep(.map-overlay),
.map-panel :deep(.map-legend),
.map-panel :deep(.timeline),
.map-panel :deep(.map-loading),
.map-panel :deep(.map-alert) {
  pointer-events: auto;
}

.map-wrap {
  position: relative;
  height: 100%;
  min-height: 580px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 0 0 8px 8px;
}
.map-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.traffic-overlay {
  z-index: 1;
  pointer-events: none;
}

.map-hint {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 2;
  max-width: min(420px, calc(100% - 340px));
  padding: 12px 16px;
  border: 1px solid rgba(33, 230, 255, 0.28);
  border-radius: 8px;
  background: rgba(1, 14, 26, 0.82);
  backdrop-filter: blur(4px);
  pointer-events: none;
}

.map-hint p {
  margin: 0;
  color: #d9f6ff;
  font-size: 13px;
  line-height: 1.6;
  text-align: center;
}

.map-loading,
.map-alert {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: min(360px, calc(100% - 48px));
  z-index: 2;
  pointer-events: none;
}

.intersection {
  stroke-width: 2;
  filter: drop-shadow(0 0 10px rgba(33, 230, 255, 0.45));
}

.vehicle {
  filter: drop-shadow(0 0 8px rgba(33, 230, 255, 0.8));
}

.vehicle.warning {
  filter: drop-shadow(0 0 9px rgba(255, 208, 90, 0.8));
}

.vehicle.danger {
  filter: drop-shadow(0 0 10px rgba(255, 77, 109, 0.9));
}

.label-box {
  fill: rgba(1, 16, 29, 0.78);
  stroke: rgba(33, 230, 255, 0.38);
}

.svg-label {
  fill: #d9f6ff;
  font-size: 12px;
  letter-spacing: 0.5px;
}

.signal {
  fill: rgba(2, 9, 18, 0.95);
  stroke: rgba(255, 255, 255, 0.22);
  stroke-width: 1;
}

.signal-red {
  fill: #ff4d6d;
}

.signal-green {
  fill: #20f6a4;
}

.signal-yellow {
  fill: #ffd05a;
}

.signal-dim {
  fill: rgba(255, 255, 255, 0.12);
}

.map-overlay {
  position: absolute;
  left: 16px;
  top: 14px;
  width: 282px;
  padding: 12px;
  border: 1px solid rgba(33, 230, 255, 0.28);
  background: rgba(1, 14, 26, 0.72);
  backdrop-filter: blur(4px);
  border-radius: 8px;
  z-index: 3;
}

.map-overlay h3 {
  margin: 0 0 8px;
  font-size: 15px;
  letter-spacing: 1px;
  color: #f1fbff;
}

.overlay-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.metric-mini {
  border: 1px solid rgba(33, 230, 255, 0.18);
  background: rgba(33, 230, 255, 0.07);
  border-radius: 6px;
  padding: 7px;
}

.metric-mini .v {
  color: #21e6ff;
  font-size: 20px;
  font-weight: 800;
}

.metric-mini .k {
  color: #78aeca;
  font-size: 12px;
  margin-top: 2px;
}

.map-legend {
  position: absolute;
  right: 16px;
  top: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(33, 230, 255, 0.26);
  background: rgba(1, 14, 26, 0.72);
  border-radius: 8px;
  font-size: 12px;
  display: grid;
  grid-template-columns: repeat(2, auto);
  gap: 7px 16px;
  color: #78aeca;
  z-index: 3;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.mini-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  box-shadow: 0 0 10px currentColor;
}

.mini-dot.cyan {
  color: #21e6ff;
  background: #21e6ff;
}

.mini-dot.yellow {
  color: #ffd05a;
  background: #ffd05a;
}

.mini-dot.red {
  color: #ff4d6d;
  background: #ff4d6d;
}

.mini-dot.green {
  color: #20f6a4;
  background: #20f6a4;
}

.timeline {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 14px;
  height: 70px;
  border: 1px solid rgba(33, 230, 255, 0.22);
  background: rgba(1, 14, 26, 0.68);
  border-radius: 8px;
  padding: 10px 14px;
  z-index: 3;
}

.timeline-head {
  display: flex;
  justify-content: space-between;
  color: #78aeca;
  font-size: 12px;
  margin-bottom: 8px;
}

.timeline-track {
  height: 10px;
  border-radius: 99px;
  background: rgba(33, 230, 255, 0.12);
  overflow: hidden;
}

.timeline-fill {
  height: 100%;
  background: linear-gradient(90deg, #20f6a4, #21e6ff, #ffd05a);
  box-shadow: 0 0 14px rgba(33, 230, 255, 0.45);
  transition: width 0.4s ease;
}
</style>
