<script setup lang="ts">
import * as echarts from 'echarts'
import { onMounted, onUnmounted, ref, watch } from 'vue'
import {
  formatCommunicationFlowParts,
  formatLogClock,
} from '../../constants/rightSidebarOptions'
import {
  RIGHT_SIDEBAR_CHARTS,
  RIGHT_SIDEBAR_CLIP_INSET_BOTTOM,
  RIGHT_SIDEBAR_CLIP_INSET_LEFT,
  RIGHT_SIDEBAR_CLIP_INSET_RIGHT,
  RIGHT_SIDEBAR_CLIP_INSET_TOP,
  RIGHT_SIDEBAR_COMMUNICATION_TABLE,
  RIGHT_SIDEBAR_CONTENT_BLOCK,
  RIGHT_SIDEBAR_CONTENT_HEIGHT,
  RIGHT_SIDEBAR_CONTENT_OFFSET,
  RIGHT_SIDEBAR_CONTENT_SCALE,
  RIGHT_SIDEBAR_CONTENT_WIDTH,
  RIGHT_SIDEBAR_DESIGN_HEIGHT,
  RIGHT_SIDEBAR_DESIGN_WIDTH,
  RIGHT_SIDEBAR_EXPORT_BUTTON,
} from '../../constants/rightSidebarLayout'
import { formatSimTime } from '../../utils/format'
import RightSidebarFrameSvg from './RightSidebarFrameSvg.vue'
import RightSidebarMetricsChrome from './RightSidebarMetricsChrome.vue'
import RightSidebarSectionHeader from './RightSidebarSectionHeader.vue'
import type { CollaborationLogEntry } from '../../types/collaboration'
import type { MetricsTimeseriesResponse } from '../../types/metrics'

const props = defineProps<{
  runId: string
  logEntries: CollaborationLogEntry[]
  collaborationLoading: boolean
  collaborationError: string | null
  wsConnected: boolean
  timeseries: MetricsTimeseriesResponse | null
  timeseriesLoading: boolean
  timeseriesError: string | null
}>()

const queueChartRef = ref<HTMLElement | null>(null)
const waitingChartRef = ref<HTMLElement | null>(null)

let queueChart: echarts.ECharts | null = null
let waitingChart: echarts.ECharts | null = null

const exportLayout = RIGHT_SIDEBAR_EXPORT_BUTTON
const chartLayout = RIGHT_SIDEBAR_CHARTS
const tableLayout = RIGHT_SIDEBAR_COMMUNICATION_TABLE
const contentBlock = RIGHT_SIDEBAR_CONTENT_BLOCK

const chartTheme = {
  lineCyan: '#21e6ff',
  lineCyanSoft: '#27c8ff',
}

function chartPlotStyle(plot: { left: number; top: number; width: number; height: number }) {
  return {
    left: `calc(${plot.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
    top: `calc(${plot.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
    width: `calc(${plot.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
    height: `calc(${plot.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
  }
}

function baseChartOption() {
  return {
    backgroundColor: 'transparent',
    grid: { left: 36, right: 12, top: 8, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(2, 16, 31, 0.92)',
      borderColor: 'rgba(33, 230, 255, 0.35)',
      textStyle: { color: '#f4fcff', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: true, color: 'rgba(157, 212, 255, 0.8)', fontSize: 11 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: true, color: 'rgba(157, 212, 255, 0.75)', fontSize: 10 },
      splitLine: { show: false },
    },
  }
}

function renderCharts() {
  const series = props.timeseries?.series ?? []
  const times = series.map((point) => formatSimTime(point.time))

  if (queueChartRef.value) {
    queueChart ??= echarts.init(queueChartRef.value)
    queueChart.setOption({
      ...baseChartOption(),
      xAxis: { ...baseChartOption().xAxis, data: times },
      series: [
        {
          type: 'line',
          smooth: true,
          symbol: 'none',
          lineStyle: { color: chartTheme.lineCyan, width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(33, 230, 255, 0.24)' },
              { offset: 1, color: 'rgba(33, 230, 255, 0.02)' },
            ]),
          },
          data: series.map((point) => point.avg_queue_length),
        },
      ],
    })
  }

  if (waitingChartRef.value) {
    waitingChart ??= echarts.init(waitingChartRef.value)
    waitingChart.setOption({
      ...baseChartOption(),
      xAxis: { ...baseChartOption().xAxis, data: times },
      series: [
        {
          type: 'line',
          smooth: true,
          symbol: 'none',
          lineStyle: { color: chartTheme.lineCyanSoft, width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(33, 230, 255, 0.28)' },
              { offset: 1, color: 'rgba(33, 230, 255, 0.02)' },
            ]),
          },
          data: series.map((point) => point.avg_waiting_time),
        },
      ],
    })
  }
}

function resizeCharts() {
  queueChart?.resize()
  waitingChart?.resize()
}

function disposeCharts() {
  queueChart?.dispose()
  waitingChart?.dispose()
  queueChart = null
  waitingChart = null
}

function handleExport() {
  if (!props.timeseries) {
    return
  }

  const blob = new Blob([JSON.stringify(props.timeseries, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `metrics-${props.runId || 'export'}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  renderCharts()
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  disposeCharts()
})

watch(
  () => props.timeseries,
  () => {
    renderCharts()
  },
  { deep: true },
)
</script>

<template>
  <section class="right-sidebar" aria-label="右侧数据面板">
    <div
      class="right-sidebar__scaler"
      :style="{
        width: `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`,
        height: `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px`,
        '--dashboard-right-sidebar-design-width': `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`,
      }"
    >
      <div
        class="right-sidebar__canvas"
        :style="{
          width: `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`,
          height: `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px`,
          '--rs-content-scale': RIGHT_SIDEBAR_CONTENT_SCALE,
        }"
      >
        <RightSidebarFrameSvg class="right-sidebar__frame" />

        <div
          class="right-sidebar__clip"
          :style="{
            top: `${RIGHT_SIDEBAR_CLIP_INSET_TOP}px`,
            left: `${RIGHT_SIDEBAR_CLIP_INSET_LEFT}px`,
            right: `${RIGHT_SIDEBAR_CLIP_INSET_RIGHT}px`,
            bottom: `${RIGHT_SIDEBAR_CLIP_INSET_BOTTOM}px`,
          }"
        >
          <div
            class="right-sidebar__content"
            :style="{
              '--rs-block-left': contentBlock.left,
              '--rs-block-width': contentBlock.width,
              '--rs-col-time': tableLayout.columns.time,
              '--rs-col-flow': tableLayout.columns.flow,
              '--rs-row-min': tableLayout.rowMinHeight,
              '--rs-table-head-top': tableLayout.head.top,
              '--rs-table-body-top': tableLayout.body.top,
              '--rs-table-body-height': tableLayout.body.height,
              '--rs-offset-x': RIGHT_SIDEBAR_CONTENT_OFFSET.x,
              '--rs-offset-y': RIGHT_SIDEBAR_CONTENT_OFFSET.y,
            }"
          >
            <RightSidebarSectionHeader title="车路云通信" variant="communication" />
            <RightSidebarSectionHeader title="量化评估结果" variant="metrics" />
            <RightSidebarMetricsChrome />

            <el-alert
              v-if="collaborationError"
              :title="collaborationError"
              type="error"
              show-icon
              :closable="false"
              class="right-sidebar__alert right-sidebar__alert--communication"
            />

            <div class="right-sidebar__table-head">
              <span>时间</span>
              <span>通信流</span>
              <span>发送信息</span>
            </div>

            <div class="right-sidebar__table-body">
              <div v-if="collaborationLoading && logEntries.length === 0" class="right-sidebar__empty">
                <el-skeleton animated :rows="5" />
              </div>

              <template v-else>
                <div
                  v-for="entry in logEntries"
                  :key="entry.id"
                  class="right-sidebar__log-row"
                >
                  <span class="right-sidebar__log-time">{{ formatLogClock(entry.timeLabel) }}</span>
                  <span class="right-sidebar__log-flow">
                    <span>{{ formatCommunicationFlowParts(entry)[0] }}</span>
                    <span class="right-sidebar__log-flow-mark" aria-hidden="true" />
                    <span>{{ formatCommunicationFlowParts(entry)[1] }}</span>
                  </span>
                  <span class="right-sidebar__log-message" :title="entry.message">
                    {{ entry.message }}
                  </span>
                </div>

                <p v-if="logEntries.length === 0" class="right-sidebar__empty-text">暂无通信记录</p>
              </template>
            </div>

            <el-alert
              v-if="timeseriesError"
              :title="timeseriesError"
              type="warning"
              show-icon
              :closable="false"
              class="right-sidebar__alert right-sidebar__alert--metrics"
            />

            <button
              type="button"
              class="right-sidebar__export-btn"
              :disabled="!timeseries"
              :style="{
                left: `calc(${exportLayout.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
                top: `calc(${exportLayout.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
                width: `calc(${exportLayout.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
                height: `calc(${exportLayout.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
              }"
              @click="handleExport"
            >
              导出
            </button>

            <div
              class="right-sidebar__chart-title right-sidebar__chart-title--queue"
              :style="{
                left: `calc(${chartLayout.queue.titleLeft} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
                top: `calc(${chartLayout.queue.titleTop} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
              }"
            >
              平均排队长度
            </div>

            <div
              v-if="timeseriesLoading && !timeseries"
              class="right-sidebar__chart-skeleton"
              :style="chartPlotStyle(chartLayout.queue.plot)"
            >
              <el-skeleton animated :rows="3" />
            </div>
            <div
              v-else-if="timeseries"
              ref="queueChartRef"
              class="right-sidebar__chart-canvas"
              :style="chartPlotStyle(chartLayout.queue.plot)"
            />

            <div
              class="right-sidebar__chart-title right-sidebar__chart-title--waiting"
              :style="{
                left: `calc(${chartLayout.waiting.titleLeft} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
                top: `calc(${chartLayout.waiting.titleTop} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
              }"
            >
              平均等待时间
            </div>

            <div
              v-if="timeseriesLoading && !timeseries"
              class="right-sidebar__chart-skeleton"
              :style="chartPlotStyle(chartLayout.waiting.plot)"
            >
              <el-skeleton animated :rows="3" />
            </div>
            <div
              v-else-if="timeseries"
              ref="waitingChartRef"
              class="right-sidebar__chart-canvas"
              :style="chartPlotStyle(chartLayout.waiting.plot)"
            />

            <div
              v-if="timeseriesLoading && !timeseries"
              class="right-sidebar__empty right-sidebar__empty--charts"
            >
              <el-skeleton animated :rows="6" />
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.right-sidebar {
  container-type: size;
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  padding-right: 4px;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  pointer-events: auto;
}

.right-sidebar__scaler {
  transform-origin: top right;
  transform: scale(
    min(1, 100cqw / var(--dashboard-right-sidebar-design-width, 600px), 100cqh / 990px)
  );
}

.right-sidebar__canvas {
  --rs-w: 465;
  --rs-h: 870;
  position: relative;
  flex-shrink: 0;
  overflow: hidden;
  color: #d8f4ff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.right-sidebar__frame {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}

.right-sidebar__clip {
  position: absolute;
  z-index: 1;
  overflow: hidden;
  pointer-events: none;
}

.right-sidebar__content {
  position: absolute;
  left: calc(var(--rs-offset-x) * 1px);
  top: calc(var(--rs-offset-y) * 1px);
  width: 465px;
  height: 870px;
  transform: scale(var(--rs-content-scale));
  transform-origin: top left;
  pointer-events: none;
}

.right-sidebar__alert {
  position: absolute;
  z-index: 6;
  pointer-events: auto;
}

.right-sidebar__alert--communication {
  left: calc(var(--rs-block-left) / var(--rs-w) * 100%);
  top: calc(82 / var(--rs-h) * 100%);
  width: calc(var(--rs-block-width) / var(--rs-w) * 100%);
}

.right-sidebar__alert--metrics {
  left: calc(var(--rs-block-left) / var(--rs-w) * 100%);
  top: calc(408 / var(--rs-h) * 100%);
  width: calc(280 / var(--rs-w) * 100%);
}

.right-sidebar__table-head,
.right-sidebar__log-row {
  display: grid;
  grid-template-columns:
    calc(var(--rs-col-time) / var(--rs-block-width) * 100%)
    calc(var(--rs-col-flow) / var(--rs-block-width) * 100%)
    minmax(0, 1fr);
  gap: calc(6 / var(--rs-block-width) * 100%);
  align-items: center;
}

.right-sidebar__table-head {
  position: absolute;
  z-index: 4;
  left: calc(var(--rs-block-left) / var(--rs-w) * 100%);
  top: calc(var(--rs-table-head-top) / var(--rs-h) * 100%);
  width: calc(var(--rs-block-width) / var(--rs-w) * 100%);
  height: calc(40 / var(--rs-h) * 100%);
  padding: 0 calc(8 / var(--rs-block-width) * 100%);
  color: var(--rs-text-label, #9dd4ff);
  font-weight: 700;
  letter-spacing: 0.8px;
  pointer-events: none;
}

.right-sidebar__table-body {
  position: absolute;
  z-index: 3;
  left: calc(var(--rs-block-left) / var(--rs-w) * 100%);
  top: calc(var(--rs-table-body-top) / var(--rs-h) * 100%);
  width: calc(var(--rs-block-width) / var(--rs-w) * 100%);
  height: calc(var(--rs-table-body-height) / var(--rs-h) * 100%);
  overflow: hidden;
  pointer-events: auto;
}

.right-sidebar__log-row {
  min-height: calc(var(--rs-row-min) * 1px);
  padding: calc(5 / var(--rs-h) * 100%) calc(8 / var(--rs-block-width) * 100%);
  border-bottom: 1px solid rgba(176, 215, 255, 0.08);
}

.right-sidebar__log-row:hover {
  background: rgba(33, 230, 255, 0.04);
}

.right-sidebar__log-time {
  color: var(--rs-text-primary, #ffffff);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.right-sidebar__log-flow {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
  color: var(--rs-text-primary, #ffffff);
  white-space: nowrap;
  overflow: hidden;
  flex-shrink: 0;
}

.right-sidebar__log-flow-mark {
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 8px solid var(--rs-accent-gold, #ffe47a);
  flex-shrink: 0;
}

.right-sidebar__log-message {
  color: var(--rs-text-secondary, #e8f6ff);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.right-sidebar__export-btn {
  position: absolute;
  z-index: 6;
  border: none;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(0, 163, 255, 0.28), rgba(4, 22, 40, 0.82));
  color: var(--rs-accent-cyan, #00a3ff);
  font-weight: 600;
  letter-spacing: 1px;
  cursor: pointer;
  pointer-events: auto;
  transition: filter 0.2s ease, opacity 0.2s ease;
}

.right-sidebar__export-btn:hover:not(:disabled) {
  filter: brightness(1.15);
}

.right-sidebar__export-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.right-sidebar__chart-title {
  position: absolute;
  z-index: 4;
  color: var(--rs-text-primary, #ffffff);
  font-weight: 700;
  letter-spacing: 0.5px;
  pointer-events: none;
}

.right-sidebar__chart-canvas,
.right-sidebar__chart-skeleton {
  position: absolute;
  z-index: 3;
  pointer-events: auto;
}

.right-sidebar__chart-canvas {
  background: transparent;
}

.right-sidebar__chart-skeleton {
  padding: 12px;
  background: rgba(2, 16, 31, 0.35);
}

.right-sidebar__empty,
.right-sidebar__empty-text {
  color: #78aeca;
}

.right-sidebar__empty {
  padding: 18px 8px;
}

.right-sidebar__empty--charts {
  position: absolute;
  left: calc(45.682 / var(--rs-w) * 100%);
  top: calc(473 / var(--rs-h) * 100%);
  width: calc(362.682 / var(--rs-w) * 100%);
  height: calc(260 / var(--rs-h) * 100%);
  pointer-events: none;
}

@media (max-width: 1320px) {
  .right-sidebar__canvas {
    width: min(600px, 100%);
    height: auto;
    aspect-ratio: 600 / 990;
  }
}
</style>
