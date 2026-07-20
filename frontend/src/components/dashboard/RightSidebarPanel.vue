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
import { EVALUATION_METRICS, METRICS_ALGORITHMS, buildAlgorithmMetricSeries, evaluationTimes, type EvaluationMetricKey } from '../../constants/metricsEvaluation'
import { formatSimTime } from '../../utils/format'
import RightSidebarFrameSvg from './RightSidebarFrameSvg.vue'
import RightSidebarSectionHeader from './RightSidebarSectionHeader.vue'
import type { CollaborationLogEntry } from '../../types/collaboration'
import type { MetricsTimeseriesResponse } from '../../types/metrics'

const props = defineProps<{ runId: string; logEntries: CollaborationLogEntry[]; collaborationLoading: boolean; collaborationError: string | null; wsConnected: boolean; timeseries: MetricsTimeseriesResponse | null; timeseriesLoading: boolean; timeseriesError: string | null }>()
const chartRefs = ref<Record<EvaluationMetricKey, HTMLElement | null>>({ queue: null, waiting: null, fuel: null })
const charts = new Map<EvaluationMetricKey, echarts.ECharts>()
const layout = RIGHT_SIDEBAR_METRICS_LAYOUT
const points = computed(() => props.timeseries?.series ?? [])
const hasRealData = computed(() => points.value.length > 0)
const comparison = computed(() => Object.fromEntries(EVALUATION_METRICS.map((metric) => [metric.key, buildAlgorithmMetricSeries(points.value, metric.key)])) as Record<EvaluationMetricKey, ReturnType<typeof buildAlgorithmMetricSeries>>)

function setChartRef(key: EvaluationMetricKey, element: unknown) { chartRefs.value[key] = element as HTMLElement | null }
function chartOption(metric: typeof EVALUATION_METRICS[number]) {
  const times = evaluationTimes(points.value).map((time) => formatSimTime(time))
  return {
    animationDuration: 450,
    backgroundColor: 'transparent',
    grid: { left: 38, right: 10, top: 8, bottom: 25 },
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(2,16,31,.96)', borderColor: 'rgba(82,194,250,.5)', textStyle: { color: '#f4fcff', fontSize: 11 }, valueFormatter: (value: number) => `${value} ${metric.unit}` },
    xAxis: { type: 'category', boundaryGap: false, data: times, axisLine: { lineStyle: { color: 'rgba(141,202,242,.28)' } }, axisTick: { show: false }, axisLabel: { color: 'rgba(188,219,241,.72)', fontSize: 10, hideOverlap: true } },
    yAxis: { type: 'value', min: 0, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: 'rgba(188,219,241,.68)', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(176,215,255,.18)', type: 'dashed' } } },
    series: comparison.value[metric.key].map((series, index) => ({ name: `${series.shortLabel} ${series.label}`, type: 'line', smooth: .42, showSymbol: false, emphasis: { focus: 'series' }, lineStyle: { color: series.color, width: index === 0 ? 2 : 1.7 }, areaStyle: index === 0 ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: `${series.color}38` }, { offset: 1, color: `${series.color}03` }]) } : undefined, data: series.values })),
  }
}
function renderCharts() {
  void nextTick(() => EVALUATION_METRICS.forEach((metric) => {
    const element = chartRefs.value[metric.key]
    if (!element) return
    const chart = charts.get(metric.key) ?? echarts.init(element)
    charts.set(metric.key, chart)
    chart.setOption(chartOption(metric), true)
  }))
}
function resizeCharts() { charts.forEach((chart) => chart.resize()) }
function disposeCharts() { charts.forEach((chart) => chart.dispose()); charts.clear() }
function handleExport() {
  const payload = { run_id: props.runId || 'demo', exported_at: new Date().toISOString(), contains_real_data: hasRealData.value, metrics: EVALUATION_METRICS.map((metric) => ({ ...metric, times: evaluationTimes(points.value), algorithms: comparison.value[metric.key] })), algorithms: METRICS_ALGORITHMS, source_notice: 'backend 表示真实基准；derived_mock 表示由基准确定性派生；estimated_mock 表示估算数据。' }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `control-evaluation-${props.runId || 'demo'}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(() => { renderCharts(); window.addEventListener('resize', resizeCharts) })
onUnmounted(() => { window.removeEventListener('resize', resizeCharts); disposeCharts() })
watch(() => props.timeseries, renderCharts, { deep: true })
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

            <div v-for="(metric, index) in EVALUATION_METRICS" :key="metric.key" class="right-sidebar__metric" :style="{ top: `${layout.metrics[index].titleTop}px` }">
              <h3>{{ metric.title }}</h3>
              <div class="right-sidebar__legend">
                <span v-for="algorithm in METRICS_ALGORITHMS" :key="algorithm.id" :title="algorithm.label"><i :style="{ background: algorithm.color }" />{{ algorithm.shortLabel }}</span>
              </div>
              <div :ref="(el) => setChartRef(metric.key, el)" class="right-sidebar__chart" />
            </div>

            <div v-if="timeseriesLoading && !hasRealData" class="right-sidebar__source-note">演示曲线加载中 · 等待真实时序</div>
            <div v-else-if="!hasRealData" class="right-sidebar__source-note">当前为确定性演示曲线</div>
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
.right-sidebar__metric { position: absolute; left: 40px; width: 355px; height: 215px; border-bottom: 1px solid rgba(97,170,224,.2); }
.right-sidebar__metric h3 { height: 27px; margin: 0; display: flex; align-items: center; color: #fff; font-size: 18px; font-weight: 800; letter-spacing: .04em; text-shadow: 0 0 8px rgba(33,230,255,.25); }
.right-sidebar__metric h3::before { content: ''; width: 4px; height: 16px; margin-right: 8px; background: #21e6ff; box-shadow: 0 0 8px #21e6ff; }
.right-sidebar__legend { height: 25px; display: flex; align-items: center; gap: 12px; padding-left: 8px; }
.right-sidebar__legend span { display: flex; align-items: center; gap: 5px; color: rgba(190,216,233,.75); font-size: 10px; white-space: nowrap; }
.right-sidebar__legend i { width: 14px; height: 3px; border-radius: 2px; box-shadow: 0 0 5px currentColor; }
.right-sidebar__chart { width: 100%; height: 161px; pointer-events: auto; }
.right-sidebar__source-note { position: absolute; z-index: 5; left: 40px; top: 770px; width: 355px; color: rgba(141,190,220,.65); font-size: 9px; text-align: right; }
.right-sidebar__export { position: absolute; z-index: 6; left: 40px; top: 786px; width: 355px; height: 38px; border: 1px solid #52c2fa; clip-path: polygon(6px 0,100% 0,100% 100%,0 100%,0 7px); background: linear-gradient(180deg,#2e519e,#3c8de7); box-shadow: inset 0 1px 0 rgba(173,235,255,.55); color: #eefaff; font: 800 17px/1 'PingFang SC','Microsoft YaHei',sans-serif; text-shadow: 0 1px 3px rgba(0,25,64,.65); cursor: pointer; pointer-events: auto; transition: filter .2s ease,transform .2s ease; }
.right-sidebar__export:hover, .right-sidebar__export:focus-visible { filter: brightness(1.14) drop-shadow(0 0 6px #52c2fa); outline: none; transform: translateY(-1px); }
@media (prefers-reduced-motion: reduce) { .right-sidebar__export { transition: none; } }
</style>
