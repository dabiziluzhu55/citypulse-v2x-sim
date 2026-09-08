<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  RIGHT_SIDEBAR_CLIP_INSET_BOTTOM,
  RIGHT_SIDEBAR_CLIP_INSET_LEFT,
  RIGHT_SIDEBAR_CLIP_INSET_RIGHT,
  RIGHT_SIDEBAR_CLIP_INSET_TOP,
  RIGHT_SIDEBAR_CONTENT_OFFSET,
  RIGHT_SIDEBAR_CONTENT_SCALE,
  RIGHT_SIDEBAR_DESIGN_HEIGHT,
  RIGHT_SIDEBAR_DESIGN_WIDTH,
  RIGHT_SIDEBAR_METRICS_COLUMN_LEFT,
  RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH,
  RIGHT_SIDEBAR_METRICS_LAYOUT,
} from '../../constants/rightSidebarLayout'
import {
  EVALUATION_METRICS,
  METRICS_ALGORITHMS,
  buildAlgorithmMetricSeries,
  evaluationAxisFromDurationSeconds,
  evaluationTimes,
  metricValue,
} from '../../constants/metricsEvaluation'
import RightSidebarFrameSvg from './RightSidebarFrameSvg.vue'
import RightSidebarSectionHeader from './RightSidebarSectionHeader.vue'
import { exportEvaluationReportPdf } from '../../api/evaluationReport.ts'
import { ApiError } from '../../api/client.ts'
import type { EvaluationComparisonRun, ScenarioComparisonContractV3 } from '../../composables/useEvaluationComparison.ts'
import type { CollaborationLogEntry } from '../../types/collaboration'
import type { EvaluationMetricKey, MetricsTimeseriesResponse } from '../../types/metrics'
import type { SimulationState } from '../../types/simulation.ts'
import {
  buildAdvantageMetrics,
  createEmptyPositiveAdvantageCache,
  formatActiveVehicleCount,
  formatAdvantagePercent,
  positiveAdvantageCacheScope,
  resolveDisplayedAdvantageMetrics,
  updatePositiveAdvantageCache,
  type AdvantageMetric,
} from '../../utils/evaluationComparisonMetrics.ts'
import {
  buildEvaluationReportFilename,
  buildEvaluationReportRequest,
  hasFinishedComparisonRun,
} from '../../utils/evaluationReport.ts'
import { formatEvaluationScopeNote } from '../../utils/scenarioDisplay.ts'
import borderSvg from '../../assets/design/dashboard/border.svg?url'
import improveIconSvg from '../../assets/design/dashboard/improve_icon.svg?url'
import baseSvg from '../../assets/design/dashboard/base.svg?url'

const props = defineProps<{
  runId: string
  activeAlgorithm: string
  logEntries: CollaborationLogEntry[]
  collaborationLoading: boolean
  collaborationError: string | null
  wsConnected: boolean
  timeseries: MetricsTimeseriesResponse | null
  timeseriesLoading: boolean
  timeseriesError: string | null
  comparisonRuns: EvaluationComparisonRun[]
  comparisonContract: ScenarioComparisonContractV3 | null
  simulationState: SimulationState | string | null
  simulationDurationSeconds?: number | null
  activeVehicleCount: number | null
  trafficState: string | null
}>()

const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
const layout = RIGHT_SIDEBAR_METRICS_LAYOUT
const activeMetricIndex = ref(0)
const points = computed(() => props.timeseries?.series ?? [])
const chartAxis = computed(() => {
  const fromContract = props.comparisonContract?.duration_seconds
  const fromSnapshot = props.simulationDurationSeconds
  const fromSeries = evaluationTimes(points.value).at(-1)
  const durationSeconds = [fromContract, fromSnapshot, fromSeries].find((value) => (
    typeof value === 'number' && Number.isFinite(value) && value > 0
  ))
  return evaluationAxisFromDurationSeconds(durationSeconds)
})
const hasRealData = computed(() => points.value.length > 0)
const canExport = computed(() => hasFinishedComparisonRun(props.comparisonRuns))
const exporting = ref(false)
const exportError = ref<string | null>(null)
const exportTitle = computed(() => (
  exporting.value
    ? '正在生成评估报告...'
    : canExport.value
      ? '导出当前场景已完成算法的终态评估报告'
      : '当前场景暂无已完成的终态评估结果'
))
const hasProvisionalData = computed(() => points.value.some((point) => point.finished === false))
const activeMetric = computed(() => EVALUATION_METRICS[activeMetricIndex.value] ?? EVALUATION_METRICS[0])
const comparison = computed(() => (
  buildAlgorithmMetricSeries(points.value, activeMetric.value.key)
))

const currentAlgorithmId = computed(() => props.activeAlgorithm
  || points.value.at(-1)?.algorithm
  || '')
const currentAlgorithmLabel = computed(() => METRICS_ALGORITHMS.find(
  (algorithm) => algorithm.id === currentAlgorithmId.value,
)?.shortLabel ?? currentAlgorithmId.value)
const latestCurrentPoint = computed(() => points.value
  .filter((point) => point.algorithm === currentAlgorithmId.value)
  .at(-1) ?? null)
const evaluationScopeNote = computed(() => {
  const point = latestCurrentPoint.value
  const scope = point?.evaluation_scope
  const contract = props.comparisonContract
  return formatEvaluationScopeNote({
    presetId: scope?.preset_id || contract?.scenario_preset_id,
    intersectionIds: scope?.intersection_ids?.length
      ? scope.intersection_ids
      : contract?.controlled_intersection_ids,
    coversFullNetwork: scope?.covers_full_network,
    hasNetworkMetrics: Boolean(point?.network_metrics),
  })
})
const rawAdvantageMetrics = computed(() => buildAdvantageMetrics(
  points.value,
  currentAlgorithmId.value,
  props.simulationState,
))
// CoV2X live presentation only:
// keep the latest positive provisional improvement;
// final values always use the raw terminal evaluation.
const lastPositiveCov2xMetrics = ref(createEmptyPositiveAdvantageCache())
const positiveHoldScopeKey = computed(() => positiveAdvantageCacheScope({
  runId: props.runId,
  algorithmId: currentAlgorithmId.value,
  contractFingerprint: props.comparisonContract ? JSON.stringify(props.comparisonContract) : '',
  cov2xSessionId: props.comparisonRuns.find((run) => run.algorithm === 'cov2x')?.sessionId ?? '',
  fixedSessionId: props.comparisonRuns.find((run) => run.algorithm === 'fixed')?.sessionId ?? '',
  timeseriesEmpty: points.value.length === 0,
}))
function clearPositiveAdvantageCache() {
  lastPositiveCov2xMetrics.value = createEmptyPositiveAdvantageCache()
}
watch(positiveHoldScopeKey, () => {
  clearPositiveAdvantageCache()
})
watch(
  rawAdvantageMetrics,
  (metrics) => {
    if (currentAlgorithmId.value !== 'cov2x') return
    lastPositiveCov2xMetrics.value = updatePositiveAdvantageCache(
      lastPositiveCov2xMetrics.value,
      metrics,
      {
        algorithmId: 'cov2x',
        finished: latestCurrentPoint.value?.finished === true,
      },
    )
  },
  { deep: true, immediate: true },
)
const displayedAdvantageMetrics = computed((): AdvantageMetric[] => {
  if (currentAlgorithmId.value !== 'cov2x' || latestCurrentPoint.value?.finished === true) {
    return rawAdvantageMetrics.value
  }
  return resolveDisplayedAdvantageMetrics(
    rawAdvantageMetrics.value,
    lastPositiveCov2xMetrics.value,
    { algorithmId: 'cov2x', finished: false },
  )
})
const trafficStateLabel = computed(() => props.trafficState?.trim() || '—')
const vehicleCountLabel = computed(() => formatActiveVehicleCount(props.activeVehicleCount))

function metricHasAnyValue(metric: EvaluationMetricKey): boolean {
  return buildAlgorithmMetricSeries(points.value, metric)
    .some((series) => series.values.some((value) => typeof value === 'number'))
}

function pointMetricStatus(metric: EvaluationMetricKey) {
  const point = latestCurrentPoint.value
  if (!point) return null
  const explicit = point.metric_status?.[metric]
  if (explicit) return explicit
  if (typeof metricValue(point, metric) === 'number') return point.finished ? 'final' : 'provisional'
  return point.finished ? 'unavailable' : 'pending'
}

function metricStatusMessage(metric: EvaluationMetricKey): string {
  const point = latestCurrentPoint.value
  if (!point) return ''
  if (typeof metricValue(point, metric) === 'number') return ''
  const status = pointMetricStatus(metric)
  if (!status) return ''
  const algorithm = currentAlgorithmLabel.value || '当前算法'
  if (metric === 'fuel_intensity' && status === 'pending') return `${algorithm}运行中，燃油消耗数据将在仿真结束后生成`
  if (metric === 'fuel_intensity' && status === 'unavailable') return `${algorithm}本次仿真暂无可用的燃油消耗数据`
  if (status === 'pending') return `${algorithm}运行中，实时指标数据暂未返回`
  return ''
}

function metricStatusTitle(metric: EvaluationMetricKey): string {
  const matcher = metric === 'fuel_intensity' ? /燃油|fuel|powertrain|里程/i : /等待|waiting|TripInfo/i
  return (latestCurrentPoint.value?.warnings ?? [])
    .filter((warning) => matcher.test(warning))
    .filter((warning, index, values) => values.indexOf(warning) === index)
    .join('\n')
}

function chartOption() {
  const metric = activeMetric.value
  const times = evaluationTimes(points.value)
  return {
    animationDuration: 450,
    backgroundColor: 'transparent',
    grid: { left: 38, right: 9, top: 10, bottom: 25 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(2,16,31,.96)',
      borderColor: 'rgba(82,194,250,.5)',
      textStyle: { color: '#f4fcff', fontSize: 11 },
      valueFormatter: (value: number | null) => value == null ? '--' : `${value} ${metric.unit}`,
    },
    xAxis: {
      type: 'value',
      min: chartAxis.value.minMinutes,
      max: chartAxis.value.maxMinutes,
      interval: chartAxis.value.intervalMinutes,
      name: '分钟',
      nameTextStyle: { color: 'rgba(188,219,241,.72)', fontSize: 9 },
      axisLine: { lineStyle: { color: 'rgba(141,202,242,.28)' } },
      axisTick: { show: false },
      axisLabel: {
        color: 'rgba(188,219,241,.72)',
        fontSize: 10,
        formatter: (value: number) => `${Number(value.toFixed(2))}`,
      },
    },
    yAxis: {
      type: 'value',
      min: 0,
      name: metric.unit,
      nameTextStyle: { color: 'rgba(188,219,241,.68)', fontSize: 9, align: 'left' },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: 'rgba(188,219,241,.68)', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(176,215,255,.18)', type: 'dashed' } },
    },
    series: comparison.value
      .filter((series) => series.values.some((value) => typeof value === 'number'))
      .map((series) => ({
      name: `${series.shortLabel} ${series.label}`,
      type: 'line',
      smooth: .42,
      connectNulls: false,
      showSymbol: false,
      emphasis: { focus: 'series' },
      lineStyle: {
        color: series.color,
        width: 1.7,
        type: series.statuses.includes('final') ? 'solid' : 'dashed',
      },
      data: series.values.map((value, index) => [times[index] / 60, value]),
    })),
  }
}

let chartRenderTimer: ReturnType<typeof setTimeout> | null = null
let lastChartRenderAt = 0

function renderChart() {
  lastChartRenderAt = performance.now()
  void nextTick(() => {
    const element = chartRef.value
    if (!element) return
    chart = chart ?? echarts.init(element)
    chart.setOption(chartOption(), { notMerge: true, lazyUpdate: true })
  })
}
function scheduleChartRender(immediate = false) {
  if (chartRenderTimer !== null) clearTimeout(chartRenderTimer)
  chartRenderTimer = null
  const remaining = Math.max(0, 1_000 - (performance.now() - lastChartRenderAt))
  if (immediate || remaining === 0) {
    renderChart()
    return
  }
  chartRenderTimer = setTimeout(() => {
    chartRenderTimer = null
    renderChart()
  }, remaining)
}
function resizeChart() { chart?.resize() }
function disposeChart() { chart?.dispose(); chart = null }
function shiftMetric(step: number) {
  const total = EVALUATION_METRICS.length
  activeMetricIndex.value = (activeMetricIndex.value + step + total) % total
  renderChart()
}
function downloadPdfBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

async function handleExport() {
  if (!canExport.value || exporting.value) return
  exporting.value = true
  exportError.value = null
  try {
    const { blob, filename } = await exportEvaluationReportPdf(
      buildEvaluationReportRequest(props.comparisonContract, props.comparisonRuns),
    )
    if (blob.size === 0 || blob.type.includes('json')) {
      throw new Error('empty-or-invalid-pdf')
    }
    downloadPdfBlob(blob, filename || buildEvaluationReportFilename(props.comparisonContract))
  } catch (cause) {
    exportError.value = cause instanceof ApiError && cause.code === 'NO_FINAL_EVALUATION_AVAILABLE'
      ? '当前场景暂无已完成的终态评估结果'
      : '评估报告生成失败，请稍后重试'
  } finally {
    exporting.value = false
  }
}

onMounted(() => { renderChart(); window.addEventListener('resize', resizeChart) })
onUnmounted(() => {
  if (chartRenderTimer !== null) clearTimeout(chartRenderTimer)
  window.removeEventListener('resize', resizeChart)
  disposeChart()
})
watch(() => [
  props.timeseries,
  activeMetricIndex.value,
  chartAxis.value.maxMinutes,
  chartAxis.value.intervalMinutes,
], () => {
  scheduleChartRender(latestCurrentPoint.value?.finished === true)
}, { deep: true })
</script>

<template>
  <section class="right-sidebar" aria-label="右侧量化评估面板">
    <div class="right-sidebar__scaler" :style="{ width: `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`, height: `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px`, '--dashboard-right-sidebar-design-width': `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`, '--dashboard-sidebar-design-height': `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px` }">
      <div class="right-sidebar__canvas" :style="{ width: `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`, height: `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px`, '--rs-content-scale': RIGHT_SIDEBAR_CONTENT_SCALE }">
        <RightSidebarFrameSvg class="right-sidebar__frame" />
        <div class="right-sidebar__clip" :style="{ top: `${RIGHT_SIDEBAR_CLIP_INSET_TOP}px`, left: `${RIGHT_SIDEBAR_CLIP_INSET_LEFT}px`, right: `${RIGHT_SIDEBAR_CLIP_INSET_RIGHT}px`, bottom: `${RIGHT_SIDEBAR_CLIP_INSET_BOTTOM}px` }">
          <div class="right-sidebar__content" :style="{ '--rs-offset-x': RIGHT_SIDEBAR_CONTENT_OFFSET.x, '--rs-offset-y': RIGHT_SIDEBAR_CONTENT_OFFSET.y }">
            <RightSidebarSectionHeader title="量化评估结果" variant="metrics" />
            <button v-if="timeseriesError" type="button" class="right-sidebar__status" :title="timeseriesError" :aria-label="timeseriesError" />

            <div
              class="right-sidebar__advantage"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.advantage.top}px`, height: `${layout.advantage.height}px` }"
            >
              <div class="right-sidebar__subsection-title">交通效能提升</div>
              <div class="right-sidebar__advantage-grid">
                <div v-for="item in displayedAdvantageMetrics" :key="item.key" class="right-sidebar__advantage-cell">
                  <img
                    :src="borderSvg"
                    class="right-sidebar__advantage-frame"
                    alt=""
                    aria-hidden="true"
                    draggable="false"
                  />
                  <div class="right-sidebar__advantage-content">
                    <img
                      :src="improveIconSvg"
                      class="right-sidebar__improve-icon"
                      alt=""
                      aria-hidden="true"
                      draggable="false"
                    />
                    <span class="right-sidebar__advantage-label">{{ item.label }}</span>
                    <strong
                      class="right-sidebar__advantage-value"
                      :class="{
                        'is-improved': item.improved === true,
                        'is-worse': item.improved === false,
                        'is-neutral': item.value === 0,
                        'is-empty': item.value == null,
                      }"
                    >
                      <em v-if="item.direction === 'up'">↑</em>
                      <em v-else-if="item.direction === 'down'">↓</em>
                      <span class="right-sidebar__hud-number">{{ formatAdvantagePercent(item.value) }}</span>
                    </strong>
                  </div>
                </div>
              </div>
            </div>

            <div
              class="right-sidebar__overview"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.trafficOverview.top}px`, height: `${layout.trafficOverview.height}px` }"
            >
              <div class="right-sidebar__overview-pane is-state">
                <span>实时交通状态</span>
                <div class="right-sidebar__overview-value">
                  <strong class="right-sidebar__overview-number">{{ trafficStateLabel }}</strong>
                  <img
                    :src="baseSvg"
                    class="right-sidebar__overview-base"
                    alt=""
                    aria-hidden="true"
                    draggable="false"
                  />
                </div>
              </div>
              <div class="right-sidebar__overview-pane is-count">
                <span>实时车辆数</span>
                <div class="right-sidebar__overview-value">
                  <strong class="right-sidebar__overview-number">{{ vehicleCountLabel }}</strong>
                  <img
                    :src="baseSvg"
                    class="right-sidebar__overview-base"
                    alt=""
                    aria-hidden="true"
                    draggable="false"
                  />
                </div>
              </div>
            </div>

            <div
              class="right-sidebar__legend-block"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.legend.top}px`, height: `${layout.legend.height}px` }"
            >
              <div class="right-sidebar__subsection-title">算法对比</div>
              <div class="right-sidebar__legend">
                <span v-for="algorithm in METRICS_ALGORITHMS" :key="algorithm.id" :title="algorithm.label">
                  <i :style="{ background: algorithm.color }" />{{ algorithm.shortLabel }}<em v-if="algorithm.id === 'cov2x'">（所提出算法）</em>
                </span>
              </div>
            </div>

            <div
              class="right-sidebar__metric"
              :style="{
                top: `${layout.chart.top}px`,
                left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`,
                width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`,
              }"
            >
              <div class="right-sidebar__metric-title">
                <h3>{{ activeMetric.title }}<small>{{ activeMetric.unit }}</small></h3>
                <el-button-group class="right-sidebar__metric-switch">
                  <el-button size="small" @click="shiftMetric(-1)">‹</el-button>
                  <el-button size="small" @click="shiftMetric(1)">›</el-button>
                </el-button-group>
              </div>
              <div ref="chartRef" class="right-sidebar__chart" :style="{ height: `${layout.chart.height}px` }" />
              <div
                v-if="metricStatusMessage(activeMetric.key)"
                class="right-sidebar__metric-status"
                :class="{ 'has-comparison-data': metricHasAnyValue(activeMetric.key) }"
                :title="metricStatusTitle(activeMetric.key)"
              >
                <strong>暂无数据</strong>
                <span>{{ metricStatusMessage(activeMetric.key) }}</span>
              </div>
            </div>

            <div
              v-if="exportError"
              class="right-sidebar__export-error"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.sourceNote.top}px` }"
            >{{ exportError }}</div>
            <div
              v-else-if="timeseriesLoading && !hasRealData"
              class="right-sidebar__source-note"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.sourceNote.top}px` }"
            >等待真实仿真评估时序</div>
            <div
              v-else-if="!hasRealData"
              class="right-sidebar__source-note"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.sourceNote.top}px` }"
            >尚无相同配置的真实算法结果</div>
            <div
              v-else-if="!hasProvisionalData"
              class="right-sidebar__source-note"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.sourceNote.top}px` }"
            >{{ evaluationScopeNote ? `仅显示相同配置的真实后端最终结果；${evaluationScopeNote}` : '仅显示相同配置的真实后端最终结果' }}</div>
            <div
              v-else-if="evaluationScopeNote"
              class="right-sidebar__source-note"
              :style="{ left: `${RIGHT_SIDEBAR_METRICS_COLUMN_LEFT}px`, width: `${RIGHT_SIDEBAR_METRICS_COLUMN_WIDTH}px`, top: `${layout.sourceNote.top}px` }"
            >{{ evaluationScopeNote }}</div>
            <button
              type="button"
              class="right-sidebar__export"
              :style="{
                left: `${layout.exportButton.left}px`,
                top: `${layout.exportButton.top}px`,
                width: `${layout.exportButton.width}px`,
                height: `${layout.exportButton.height}px`,
              }"
              :disabled="!canExport || exporting"
              :title="exportTitle"
              @click="handleExport"
            >{{ exporting ? '正在生成评估报告...' : '导出当前场景管控评估结果' }}</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.right-sidebar {
  --rs-cyan: #21e6ff;
  --rs-text-primary: #f2fbff;
  --rs-number-top: #ffffff;
  --rs-number-mid: #bff6ff;
  --rs-number-bottom: #4fc8ff;
  --rs-number-glow: rgba(42, 202, 255, .55);
  container-type: size;
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  width: 100%;
  height: 100%;
  padding-right: 4px;
  overflow: hidden;
  pointer-events: none;
}
.right-sidebar__scaler { transform-origin: top right; transform: scale(min(1,100cqw / var(--dashboard-right-sidebar-design-width,600px),100cqh / var(--dashboard-sidebar-design-height,990px))); pointer-events: auto; }
.right-sidebar__canvas { position: relative; flex-shrink: 0; overflow: hidden; color: #d8f4ff; font-family: 'PingFang SC','Microsoft YaHei',sans-serif; }
.right-sidebar__frame { position: absolute; inset: 0; z-index: 0; pointer-events: none; }
.right-sidebar__clip { position: absolute; z-index: 1; overflow: hidden; pointer-events: none; }
.right-sidebar__content { position: absolute; left: calc(var(--rs-offset-x) * 1px); top: calc(var(--rs-offset-y) * 1px); width: 465px; height: 870px; transform: scale(var(--rs-content-scale)); transform-origin: top left; pointer-events: none; }
.right-sidebar__status { position: absolute; z-index: 8; top: 48px; right: 36px; width: 8px; height: 8px; padding: 0; border: 0; border-radius: 50%; background: #ffb458; box-shadow: 0 0 8px #ffb458; pointer-events: auto; cursor: help; }

.right-sidebar__subsection-title {
  display: flex;
  align-items: center;
  min-width: 0;
  height: 22px;
  color: var(--rs-text-primary);
  font-size: 18px;
  font-weight: 800;
  letter-spacing: .04em;
  text-shadow: 0 0 8px rgba(33, 230, 255, .25);
  white-space: nowrap;
}
.right-sidebar__subsection-title::before {
  content: '';
  flex: 0 0 4px;
  width: 4px;
  height: 16px;
  margin-right: 8px;
  background: var(--rs-cyan);
  box-shadow: 0 0 6px rgba(33, 230, 255, .75);
}

.right-sidebar__advantage {
  position: absolute;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.right-sidebar__advantage-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(2, auto);
  gap: 14px 10px;
  align-content: start;
  min-width: 0;
  min-height: 0;
}
.right-sidebar__advantage-cell {
  position: relative;
  overflow: visible;
  width: 100%;
  aspect-ratio: 173 / 73;
  min-width: 0;
}
.right-sidebar__advantage-frame {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
  user-select: none;
}
.right-sidebar__advantage-content {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-content: start;
  align-items: center;
  justify-content: start;
  column-gap: 6px;
  row-gap: 5px;
  width: 100%;
  height: 100%;
  padding: 6px 10px 6px 10px;
  text-align: left;
}
.right-sidebar__improve-icon {
  grid-column: 1;
  grid-row: 1 / span 2;
  align-self: start;
  display: block;
  width: auto;
  height: calc(19px + 5px + 28px);
  object-fit: contain;
  object-position: left center;
  pointer-events: none;
  user-select: none;
}
.right-sidebar__advantage-label {
  grid-column: 2;
  grid-row: 1;
  height: 19px;
  color: #accde6;
  font-size: 13px;
  font-weight: 600;
  line-height: 19px;
  letter-spacing: 0;
  white-space: nowrap;
}
.right-sidebar__advantage-value {
  position: relative;
  grid-column: 2;
  grid-row: 2;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
  min-height: 28px;
  font-size: 30px;
  font-weight: 800;
  letter-spacing: .01em;
  line-height: 1;
}
.right-sidebar__advantage-value::after,
.right-sidebar__overview-number::after {
  content: '';
  position: absolute;
  left: 8%;
  right: 8%;
  top: 45%;
  z-index: 2;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .18), transparent);
  opacity: .35;
  pointer-events: none;
}
.right-sidebar__hud-number,
.right-sidebar__overview-number {
  position: relative;
  z-index: 1;
  background: linear-gradient(180deg, #ffffff 0%, #dffaff 28%, #9aeaff 62%, #56cfff 100%);
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  -webkit-text-stroke: .25px rgba(221, 250, 255, .70);
  color: var(--rs-number-mid);
  font-variant-numeric: tabular-nums lining-nums;
  font-feature-settings: 'tnum' 1, 'lnum' 1;
  font-family: 'Arial Narrow', 'Roboto Condensed', 'DIN Alternate', Bahnschrift, 'Microsoft YaHei', sans-serif;
  text-shadow: 0 1px 0 rgba(255, 255, 255, .28);
  filter:
    drop-shadow(0 0 2px rgba(255, 255, 255, .55))
    drop-shadow(0 0 5px rgba(56, 218, 255, .45))
    drop-shadow(0 0 10px rgba(33, 121, 255, .22));
}
.right-sidebar__advantage-value em {
  position: relative;
  z-index: 1;
  font-style: normal;
  font-size: 20px;
  -webkit-text-fill-color: currentColor;
  -webkit-text-stroke: 0;
  background: none;
  filter: drop-shadow(0 0 4px currentColor);
}
.right-sidebar__advantage-value.is-improved em { color: #55E69A; }
.right-sidebar__advantage-value.is-worse em { color: #FF5B64; }
.right-sidebar__advantage-value.is-neutral .right-sidebar__hud-number,
.right-sidebar__advantage-value.is-empty .right-sidebar__hud-number {
  background: none;
  -webkit-text-fill-color: rgba(188, 219, 241, .42);
  -webkit-text-stroke: 0;
  color: rgba(188, 219, 241, .42);
  filter: none;
  text-shadow: none;
  font-size: 22px;
}
.right-sidebar__advantage-value.is-neutral::after,
.right-sidebar__advantage-value.is-empty::after {
  content: none;
}

.right-sidebar__overview {
  position: absolute;
  display: grid;
  grid-template-columns: 1fr 1fr;
  align-items: center;
  min-width: 0;
}
.right-sidebar__overview-pane {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-width: 0;
  padding: 2px 8px 0;
  text-align: center;
}
.right-sidebar__overview-pane span {
  height: 30px;
  color: var(--rs-text-primary);
  font-size: 18px;
  font-weight: 800;
  letter-spacing: .04em;
  line-height: 22px;
  text-shadow: 0 0 8px rgba(33, 230, 255, .25);
  white-space: nowrap;
}
.right-sidebar__overview-value {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  width: 100%;
}
.right-sidebar__overview-value strong {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 30px;
  max-width: 100%;
  font-size: 28px;
  font-weight: 800;
  letter-spacing: .01em;
  line-height: 1;
}
.right-sidebar__overview-pane.is-state .right-sidebar__overview-number {
  font-family: 'Microsoft YaHei', 'PingFang SC', Bahnschrift, sans-serif;
}
.right-sidebar__overview-pane.is-count .right-sidebar__overview-number {
  font-family: Bahnschrift, 'Arial Narrow', 'Roboto Condensed', 'DIN Alternate', 'Microsoft YaHei', sans-serif;
}
.right-sidebar__overview-base {
  position: relative;
  z-index: 0;
  display: block;
  width: 118px;
  height: auto;
  margin-top: -28px;
  pointer-events: none;
  user-select: none;
}

.right-sidebar__legend-block {
  position: absolute;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.right-sidebar__legend {
  display: grid;
  flex: 1;
  grid-template-columns: repeat(3, 1fr);
  grid-template-rows: repeat(2, auto);
  align-items: center;
  justify-items: stretch;
  gap: 8px 12px;
  min-width: 0;
}
.right-sidebar__legend span {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  min-width: 0;
  gap: 6px;
  color: rgba(214, 232, 246, .82);
  font-size: 10px;
  white-space: nowrap;
}
.right-sidebar__legend i {
  flex: 0 0 16px;
  width: 16px;
  height: 3px;
  border-radius: 3px;
  box-shadow: 0 0 5px currentColor;
}
.right-sidebar__legend em {
  margin-left: 1px;
  color: rgba(188, 219, 241, .62);
  font-size: 13px;
  font-style: normal;
  font-weight: 500;
}

.right-sidebar__metric { position: absolute; }
.right-sidebar__metric-title {
  height: 28px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.right-sidebar__metric h3 {
  margin: 0;
  display: flex;
  flex: 1;
  align-items: center;
  min-width: 0;
  color: var(--rs-text-primary);
  font-size: 16px;
  font-weight: 800;
  letter-spacing: .04em;
  text-shadow: 0 0 8px rgba(33, 230, 255, .25);
  white-space: nowrap;
}
.right-sidebar__metric h3::before {
  content: '';
  flex: 0 0 4px;
  width: 4px;
  height: 16px;
  margin-right: 8px;
  background: var(--rs-cyan);
  box-shadow: 0 0 6px rgba(33, 230, 255, .75);
}
.right-sidebar__metric h3::after {
  content: '';
  flex: 1 1 auto;
  min-width: 16px;
  height: 1px;
  margin: 0 8px 0 10px;
  background: linear-gradient(90deg, rgba(90, 214, 255, .7), rgba(33, 230, 255, 0));
}
.right-sidebar__metric h3 small {
  margin-left: 8px;
  color: rgba(188, 219, 241, .72);
  font-size: 10px;
  font-weight: 600;
}
.right-sidebar__metric-switch { flex: 0 0 auto; pointer-events: auto; }
.right-sidebar__metric-switch :deep(.el-button) {
  width: 28px;
  height: 22px;
  padding: 0;
  border-color: rgba(33, 195, 255, .55);
  background: rgba(5, 28, 52, .65);
  color: #dff9ff;
}
.right-sidebar__metric-switch :deep(.el-button:hover),
.right-sidebar__metric-switch :deep(.el-button:focus-visible) {
  background: rgba(20, 93, 150, .45);
  box-shadow: 0 0 6px rgba(33, 230, 255, .28);
  color: #f4fcff;
}
.right-sidebar__chart { width: 100%; pointer-events: auto; }
.right-sidebar__metric-status {
  position: absolute;
  left: 38px;
  right: 10px;
  top: 52px;
  bottom: 30px;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  background: rgba(5, 18, 39, .68);
  color: rgba(188, 219, 241, .82);
  font-size: 10px;
  text-align: center;
  pointer-events: auto;
}
.right-sidebar__metric-status strong { color: #d8f4ff; font-size: 18px; letter-spacing: 0; }
.right-sidebar__metric-status.has-comparison-data {
  left: auto;
  right: 10px;
  top: 34px;
  bottom: auto;
  width: 170px;
  min-height: 34px;
  padding: 5px 8px;
  border: 1px solid rgba(82, 194, 250, .24);
  background: rgba(5, 18, 39, .88);
  align-items: flex-end;
}
.right-sidebar__metric-status.has-comparison-data strong { display: none; }
.right-sidebar__source-note { position: absolute; z-index: 5; color: rgba(141, 190, 220, .65); font-size: 9px; text-align: center; }
.right-sidebar__export-error { position: absolute; z-index: 6; color: #ffb458; font-size: 9px; text-align: center; pointer-events: none; }
.right-sidebar__export {
  position: absolute;
  z-index: 6;
  border: 1px solid #52c2fa;
  clip-path: polygon(6px 0, 100% 0, 100% 100%, 0 100%, 0 7px);
  background: linear-gradient(180deg, #2e519e, #3c8de7);
  box-shadow: inset 0 1px 0 rgba(173, 235, 255, .55);
  color: #eefaff;
  font: 800 17px/1 'PingFang SC','Microsoft YaHei',sans-serif;
  text-shadow: 0 1px 3px rgba(0, 25, 64, .65);
  cursor: pointer;
  pointer-events: auto;
  transition: filter .2s ease, transform .2s ease;
}
.right-sidebar__export:hover, .right-sidebar__export:focus-visible { filter: brightness(1.14) drop-shadow(0 0 6px #52c2fa); outline: none; transform: translateY(-1px); }
.right-sidebar__export:disabled { opacity: .45; filter: grayscale(.45); box-shadow: none; cursor: not-allowed; transform: none; }
.right-sidebar__export:disabled:hover, .right-sidebar__export:disabled:focus-visible { filter: grayscale(.45); transform: none; }
@media (prefers-reduced-motion: reduce) { .right-sidebar__export { transition: none; } }
</style>
