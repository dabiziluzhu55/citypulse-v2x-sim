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
  RIGHT_SIDEBAR_METRICS_LAYOUT,
} from '../../constants/rightSidebarLayout'
import { EVALUATION_AXIS, EVALUATION_METRICS, METRICS_ALGORITHMS, buildAlgorithmMetricSeries, evaluationTimes, type EvaluationMetricKey } from '../../constants/metricsEvaluation'
import RightSidebarFrameSvg from './RightSidebarFrameSvg.vue'
import RightSidebarSectionHeader from './RightSidebarSectionHeader.vue'
import type { CollaborationLogEntry } from '../../types/collaboration'
import type { MetricsTimeseriesResponse } from '../../types/metrics'

const props = defineProps<{ runId: string; activeAlgorithm: string; logEntries: CollaborationLogEntry[]; collaborationLoading: boolean; collaborationError: string | null; wsConnected: boolean; timeseries: MetricsTimeseriesResponse | null; timeseriesLoading: boolean; timeseriesError: string | null }>()
const chartRefs = ref<Record<EvaluationMetricKey, HTMLElement | null>>({ queue: null, waiting: null, fuel: null })
const charts = new Map<EvaluationMetricKey, echarts.ECharts>()
const layout = RIGHT_SIDEBAR_METRICS_LAYOUT
const points = computed(() => props.timeseries?.series ?? [])
const hasRealData = computed(() => points.value.length > 0)
const hasProvisionalData = computed(() => points.value.some((point) => point.finished === false))
const comparison = computed(() => Object.fromEntries(EVALUATION_METRICS.map((metric) => [metric.key, buildAlgorithmMetricSeries(points.value, metric.key)])) as Record<EvaluationMetricKey, ReturnType<typeof buildAlgorithmMetricSeries>>)
function algorithmHasData(algorithmId: string): boolean {
  return points.value.some((point) => point.algorithm === algorithmId)
}

const backendWarnings = computed(() => points.value
  .flatMap((point) => point.warnings ?? [])
  .filter((warning, index, values) => values.indexOf(warning) === index))

const currentAlgorithmId = computed(() => props.activeAlgorithm
  || points.value.at(-1)?.algorithm
  || '')
const currentAlgorithmLabel = computed(() => METRICS_ALGORITHMS.find(
  (algorithm) => algorithm.id === currentAlgorithmId.value,
)?.shortLabel ?? currentAlgorithmId.value)
const latestCurrentPoint = computed(() => points.value
  .filter((point) => point.algorithm === currentAlgorithmId.value)
  .at(-1) ?? null)

function metricHasAnyValue(metric: EvaluationMetricKey): boolean {
  return comparison.value[metric].some((series) => series.values.some((value) => (
    typeof value === 'number'
  )))
}

function pointMetricValue(metric: EvaluationMetricKey): number | null {
  const point = latestCurrentPoint.value
  if (!point) return null
  if (metric === 'queue') return point.avg_queue_length
  if (metric === 'waiting') return point.avg_waiting_time
  return typeof point.fuel_consumption === 'number' ? point.fuel_consumption : null
}

function pointMetricStatus(metric: EvaluationMetricKey) {
  const point = latestCurrentPoint.value
  if (!point) return null
  const explicit = point.metric_status?.[metric]
  if (explicit) return explicit
  if (typeof pointMetricValue(metric) === 'number') return point.finished ? 'final' : 'provisional'
  return point.finished ? 'unavailable' : 'pending'
}

function metricStatusMessage(metric: EvaluationMetricKey): string {
  const status = pointMetricStatus(metric)
  if (!status || typeof pointMetricValue(metric) === 'number') return ''
  const algorithm = currentAlgorithmLabel.value || '当前算法'
  if (metric === 'fuel' && status === 'pending') return `${algorithm}运行中，等待 TripInfo 终态回填`
  if (metric === 'fuel' && status === 'unavailable') return `${algorithm}终态未提供可用燃油强度`
  if (status === 'pending') return `${algorithm}实时口径尚未回填`
  return ''
}

function metricStatusTitle(metric: EvaluationMetricKey): string {
  const matcher = metric === 'fuel' ? /燃油|fuel|powertrain|里程/i : /等待|waiting|TripInfo/i
  return (latestCurrentPoint.value?.warnings ?? [])
    .filter((warning) => matcher.test(warning))
    .filter((warning, index, values) => values.indexOf(warning) === index)
    .join('\n')
}

function setChartRef(key: EvaluationMetricKey, element: unknown) { chartRefs.value[key] = element as HTMLElement | null }
function chartOption(metric: typeof EVALUATION_METRICS[number]) {
  const times = evaluationTimes(points.value)
  return {
    animationDuration: 450,
    backgroundColor: 'transparent',
    grid: { left: 38, right: 10, top: 8, bottom: 25 },
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(2,16,31,.96)', borderColor: 'rgba(82,194,250,.5)', textStyle: { color: '#f4fcff', fontSize: 11 }, valueFormatter: (value: number | null) => value == null ? '--' : `${value} ${metric.unit}` },
    xAxis: { type: 'value', min: EVALUATION_AXIS.minMinutes, max: EVALUATION_AXIS.maxMinutes, interval: EVALUATION_AXIS.intervalMinutes, name: '分钟', nameTextStyle: { color: 'rgba(188,219,241,.72)', fontSize: 9 }, axisLine: { lineStyle: { color: 'rgba(141,202,242,.28)' } }, axisTick: { show: false }, axisLabel: { color: 'rgba(188,219,241,.72)', fontSize: 10 } },
    yAxis: { type: 'value', min: 0, name: metric.unit, nameTextStyle: { color: 'rgba(188,219,241,.68)', fontSize: 9, align: 'left' }, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: 'rgba(188,219,241,.68)', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(176,215,255,.18)', type: 'dashed' } } },
    series: comparison.value[metric.key].map((series) => ({ name: `${series.shortLabel} ${series.label}`, type: 'line', smooth: .42, connectNulls: false, showSymbol: false, emphasis: { focus: 'series' }, lineStyle: { color: series.color, width: 1.9, type: series.statuses.includes('final') ? 'solid' : 'dashed' }, data: series.values.map((value, index) => [times[index] / 60, value]) })),
  }
}
let chartRenderTimer: ReturnType<typeof setTimeout> | null = null
let lastChartRenderAt = 0

function renderCharts() {
  lastChartRenderAt = performance.now()
  void nextTick(() => EVALUATION_METRICS.forEach((metric) => {
    const element = chartRefs.value[metric.key]
    if (!element) return
    const chart = charts.get(metric.key) ?? echarts.init(element)
    charts.set(metric.key, chart)
    chart.setOption(chartOption(metric), { notMerge: true, lazyUpdate: true })
  }))
}
function scheduleChartRender(immediate = false) {
  if (chartRenderTimer !== null) clearTimeout(chartRenderTimer)
  chartRenderTimer = null
  const remaining = Math.max(0, 1_000 - (performance.now() - lastChartRenderAt))
  if (immediate || remaining === 0) {
    renderCharts()
    return
  }
  chartRenderTimer = setTimeout(() => {
    chartRenderTimer = null
    renderCharts()
  }, remaining)
}
function resizeCharts() { charts.forEach((chart) => chart.resize()) }
function disposeCharts() { charts.forEach((chart) => chart.dispose()); charts.clear() }
function handleExport() {
  const payload = {
    run_id: props.runId || 'unassigned',
    exported_at: new Date().toISOString(),
    contains_real_data: hasRealData.value,
    contains_provisional_data: hasProvisionalData.value,
    finished: points.value.some((point) => point.finished === true),
    x_axis: {
      min_minutes: EVALUATION_AXIS.minMinutes,
      max_minutes: EVALUATION_AXIS.maxMinutes,
      interval_minutes: EVALUATION_AXIS.intervalMinutes,
    },
    metrics: EVALUATION_METRICS.map((metric) => ({
      ...metric,
      times_seconds: evaluationTimes(points.value),
      algorithms: comparison.value[metric.key],
    })),
    backend_points: points.value,
    warnings: backendWarnings.value,
    algorithms: METRICS_ALGORITHMS,
    source_notice: '仅包含相同配置下由后端仿真实际返回的算法评估数据；pending、provisional、final、unavailable 分别表示等待回填、实时临时值、TripInfo 最终值和后端不可用。',
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `control-evaluation-${props.runId || 'demo'}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(() => { renderCharts(); window.addEventListener('resize', resizeCharts) })
onUnmounted(() => {
  if (chartRenderTimer !== null) clearTimeout(chartRenderTimer)
  window.removeEventListener('resize', resizeCharts)
  disposeCharts()
})
watch(() => props.timeseries, () => {
  scheduleChartRender(latestCurrentPoint.value?.finished === true)
}, { deep: true })
</script>

<template>
  <section class="right-sidebar" aria-label="右侧量化评估面板">
    <div class="right-sidebar__scaler" :style="{ width: `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`, height: `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px`, '--dashboard-right-sidebar-design-width': `${RIGHT_SIDEBAR_DESIGN_WIDTH}px` }">
      <div class="right-sidebar__canvas" :style="{ width: `${RIGHT_SIDEBAR_DESIGN_WIDTH}px`, height: `${RIGHT_SIDEBAR_DESIGN_HEIGHT}px`, '--rs-content-scale': RIGHT_SIDEBAR_CONTENT_SCALE }">
        <RightSidebarFrameSvg class="right-sidebar__frame" />
        <div class="right-sidebar__clip" :style="{ top: `${RIGHT_SIDEBAR_CLIP_INSET_TOP}px`, left: `${RIGHT_SIDEBAR_CLIP_INSET_LEFT}px`, right: `${RIGHT_SIDEBAR_CLIP_INSET_RIGHT}px`, bottom: `${RIGHT_SIDEBAR_CLIP_INSET_BOTTOM}px` }">
          <div class="right-sidebar__content" :style="{ '--rs-offset-x': RIGHT_SIDEBAR_CONTENT_OFFSET.x, '--rs-offset-y': RIGHT_SIDEBAR_CONTENT_OFFSET.y }">
            <RightSidebarSectionHeader title="量化评估结果" variant="metrics" />
            <button v-if="timeseriesError" type="button" class="right-sidebar__status" :title="timeseriesError" :aria-label="timeseriesError" />

            <div class="right-sidebar__legend">
              <span v-for="algorithm in METRICS_ALGORITHMS" :key="algorithm.id" :class="{ 'is-pending': !algorithmHasData(algorithm.id) }" :title="algorithm.label"><i :style="{ background: algorithm.color }" />{{ algorithm.shortLabel }}<em v-if="!algorithmHasData(algorithm.id)">待运行</em></span>
            </div>

            <div v-for="(metric, index) in EVALUATION_METRICS" :key="metric.key" class="right-sidebar__metric" :style="{ top: `${layout.metrics[index].titleTop}px` }">
              <h3>{{ metric.title }}<small>{{ metric.unit }}</small></h3>
              <div :ref="(el) => setChartRef(metric.key, el)" class="right-sidebar__chart" />
              <div
                v-if="metricStatusMessage(metric.key)"
                class="right-sidebar__metric-status"
                :class="{ 'has-comparison-data': metricHasAnyValue(metric.key) }"
                :title="metricStatusTitle(metric.key)"
              >
                <strong>--</strong>
                <span>{{ metricStatusMessage(metric.key) }}</span>
              </div>
            </div>

            <div v-if="timeseriesLoading && !hasRealData" class="right-sidebar__source-note">等待真实仿真评估时序</div>
            <div v-else-if="!hasRealData" class="right-sidebar__source-note">尚无相同配置的真实算法结果</div>
            <div v-else-if="hasProvisionalData" class="right-sidebar__source-note">虚线为实时临时值，终态以 TripInfo 回填为准</div>
            <div v-else class="right-sidebar__source-note">仅显示相同配置的真实后端最终结果</div>
            <button type="button" class="right-sidebar__export" @click="handleExport">导出当前场景管控评估结果</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.right-sidebar { container-type: size; display: flex; justify-content: flex-end; align-items: flex-start; width: 100%; height: 100%; padding-right: 4px; overflow: hidden; pointer-events: auto; }
.right-sidebar__scaler { transform-origin: top right; transform: scale(min(1,100cqw / var(--dashboard-right-sidebar-design-width,600px),100cqh / 990px)); }
.right-sidebar__canvas { position: relative; flex-shrink: 0; overflow: hidden; color: #d8f4ff; font-family: 'PingFang SC','Microsoft YaHei',sans-serif; }
.right-sidebar__frame { position: absolute; inset: 0; z-index: 0; pointer-events: none; }
.right-sidebar__clip { position: absolute; z-index: 1; overflow: hidden; pointer-events: none; }
.right-sidebar__content { position: absolute; left: calc(var(--rs-offset-x) * 1px); top: calc(var(--rs-offset-y) * 1px); width: 465px; height: 870px; transform: scale(var(--rs-content-scale)); transform-origin: top left; pointer-events: none; }
.right-sidebar__status { position: absolute; z-index: 8; top: 48px; right: 36px; width: 8px; height: 8px; padding: 0; border: 0; border-radius: 50%; background: #ffb458; box-shadow: 0 0 8px #ffb458; pointer-events: auto; cursor: help; }
.right-sidebar__metric { position: absolute; left: 55px; width: 355px; height: 208px; border-bottom: 1px solid rgba(97,170,224,.2); }
.right-sidebar__metric h3 { height: 27px; margin: 0; display: flex; align-items: center; color: #fff; font-size: 18px; font-weight: 800; letter-spacing: .04em; text-shadow: 0 0 8px rgba(33,230,255,.25); }
.right-sidebar__metric h3::before { content: ''; width: 4px; height: 16px; margin-right: 8px; background: #21e6ff; box-shadow: 0 0 8px #21e6ff; }
.right-sidebar__metric h3 small { margin-left: 8px; color: rgba(188,219,241,.72); font-size: 10px; font-weight: 600; }
.right-sidebar__legend { position: absolute; left: 55px; top: 80px; width: 355px; height: 25px; display: flex; align-items: center; justify-content: center; gap: 12px; }
.right-sidebar__legend span { display: flex; align-items: center; gap: 5px; color: rgba(190,216,233,.75); font-size: 10px; white-space: nowrap; }
.right-sidebar__legend i { width: 14px; height: 3px; border-radius: 2px; box-shadow: 0 0 5px currentColor; }
.right-sidebar__legend span.is-pending { opacity: .48; }
.right-sidebar__legend em { color: #7e9bb0; font-size: 8px; font-style: normal; }
.right-sidebar__chart { width: 100%; height: 174px; pointer-events: auto; }
.right-sidebar__metric-status { position: absolute; left: 38px; right: 10px; top: 52px; bottom: 30px; z-index: 2; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px; background: rgba(5,18,39,.68); color: rgba(188,219,241,.82); font-size: 10px; text-align: center; pointer-events: auto; }
.right-sidebar__metric-status strong { color: #d8f4ff; font-size: 18px; letter-spacing: 0; }
.right-sidebar__metric-status.has-comparison-data { left: auto; right: 10px; top: 34px; bottom: auto; width: 170px; min-height: 34px; padding: 5px 8px; border: 1px solid rgba(82,194,250,.24); background: rgba(5,18,39,.88); align-items: flex-end; }
.right-sidebar__metric-status.has-comparison-data strong { display: none; }
.right-sidebar__source-note { position: absolute; z-index: 5; left: 55px; top: 770px; width: 355px; color: rgba(141,190,220,.65); font-size: 9px; text-align: right; }
.right-sidebar__export { position: absolute; z-index: 6; left: 55px; top: 786px; width: 355px; height: 38px; border: 1px solid #52c2fa; clip-path: polygon(6px 0,100% 0,100% 100%,0 100%,0 7px); background: linear-gradient(180deg,#2e519e,#3c8de7); box-shadow: inset 0 1px 0 rgba(173,235,255,.55); color: #eefaff; font: 800 17px/1 'PingFang SC','Microsoft YaHei',sans-serif; text-shadow: 0 1px 3px rgba(0,25,64,.65); cursor: pointer; pointer-events: auto; transition: filter .2s ease,transform .2s ease; }
.right-sidebar__export:hover, .right-sidebar__export:focus-visible { filter: brightness(1.14) drop-shadow(0 0 6px #52c2fa); outline: none; transform: translateY(-1px); }
@media (prefers-reduced-motion: reduce) { .right-sidebar__export { transition: none; } }
</style>
