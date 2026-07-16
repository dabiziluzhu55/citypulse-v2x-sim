<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import OverviewStatCard from '../overview/OverviewStatCard.vue'
import { formatAlgorithmLabel, REALTIME_METRIC_ITEMS } from '../../constants/metricsOptions'
import { formatNumber, formatSimTime } from '../../utils/format'
import type {
  ExperimentComparisonResponse,
  MetricComparisonRow,
  MetricsTimeseriesResponse,
  RealtimeMetricsResponse,
} from '../../types/metrics'

const props = defineProps<{
  runId: string
  experimentId: string
  currentAlgorithmId: string
  realtime: RealtimeMetricsResponse | null
  comparison: ExperimentComparisonResponse | null
  timeseries: MetricsTimeseriesResponse | null
  comparisonRows: MetricComparisonRow[]
  realtimeLoading: boolean
  comparisonLoading: boolean
  timeseriesLoading: boolean
  realtimeError: string | null
  comparisonError: string | null
  timeseriesError: string | null
}>()

const emit = defineEmits<{ refresh: [] }>()

const waitingChartRef = ref<HTMLElement | null>(null)
const queueChartRef = ref<HTMLElement | null>(null)
const throughputChartRef = ref<HTMLElement | null>(null)
const algorithmChartRef = ref<HTMLElement | null>(null)

let waitingChart: echarts.ECharts | null = null
let queueChart: echarts.ECharts | null = null
let throughputChart: echarts.ECharts | null = null
let algorithmChart: echarts.ECharts | null = null

const chartTheme = {
  text: '#78aeca',
  axis: 'rgba(33, 230, 255, 0.35)',
  grid: 'rgba(33, 230, 255, 0.08)',
  lineCyan: '#21e6ff',
  lineYellow: '#ffd05a',
  lineGreen: '#20f6a4',
  barColors: ['#21e6ff', '#247cff', '#ffd05a', '#20f6a4'],
}

const baselineAlgorithmLabel = computed(() =>
  formatAlgorithmLabel(props.comparisonRows[0]?.baselineLabel ?? 'fixed_time'),
)

const currentAlgorithmLabel = computed(() =>
  formatAlgorithmLabel(
    props.currentAlgorithmId || props.comparisonRows[0]?.currentLabel || 'current',
  ),
)

function baseChartOption(title: string, yName: string) {
  return {
    backgroundColor: 'transparent',
    title: {
      text: title,
      left: 0,
      top: 0,
      textStyle: { color: '#21e6ff', fontSize: 12, fontWeight: 600 },
    },
    grid: { left: 48, right: 16, top: 36, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(2, 16, 31, 0.92)',
      borderColor: 'rgba(33, 230, 255, 0.35)',
      textStyle: { color: '#f4fcff' },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      axisLine: { lineStyle: { color: chartTheme.axis } },
      axisLabel: { color: chartTheme.text, fontSize: 11 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: yName,
      nameTextStyle: { color: chartTheme.text, fontSize: 11 },
      axisLine: { show: false },
      axisLabel: { color: chartTheme.text, fontSize: 11 },
      splitLine: { lineStyle: { color: chartTheme.grid } },
    },
  }
}

function renderLineCharts() {
  const series = props.timeseries?.series ?? []
  const times = series.map((point) => formatSimTime(point.time))

  if (waitingChartRef.value) {
    waitingChart ??= echarts.init(waitingChartRef.value)
    waitingChart.setOption({
      ...baseChartOption('平均等待时间随时间变化', 's'),
      xAxis: { ...baseChartOption('', '').xAxis, data: times },
      series: [
        {
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: chartTheme.lineCyan, width: 2 },
          itemStyle: { color: chartTheme.lineCyan },
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

  if (queueChartRef.value) {
    queueChart ??= echarts.init(queueChartRef.value)
    queueChart.setOption({
      ...baseChartOption('平均排队长度随时间变化', 'veh'),
      xAxis: { ...baseChartOption('', '').xAxis, data: times },
      series: [
        {
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: chartTheme.lineYellow, width: 2 },
          itemStyle: { color: chartTheme.lineYellow },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(255, 208, 90, 0.24)' },
              { offset: 1, color: 'rgba(255, 208, 90, 0.02)' },
            ]),
          },
          data: series.map((point) => point.avg_queue_length),
        },
      ],
    })
  }

  if (throughputChartRef.value) {
    throughputChart ??= echarts.init(throughputChartRef.value)
    throughputChart.setOption({
      ...baseChartOption('通行量随时间变化', 'veh/h'),
      xAxis: { ...baseChartOption('', '').xAxis, data: times },
      series: [
        {
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: chartTheme.lineGreen, width: 2 },
          itemStyle: { color: chartTheme.lineGreen },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(32, 246, 164, 0.24)' },
              { offset: 1, color: 'rgba(32, 246, 164, 0.02)' },
            ]),
          },
          data: series.map((point) => point.throughput),
        },
      ],
    })
  }
}

function renderAlgorithmChart() {
  const results = props.comparison?.results ?? []
  if (!algorithmChartRef.value || results.length === 0) {
    return
  }

  algorithmChart ??= echarts.init(algorithmChartRef.value)
  algorithmChart.setOption({
    backgroundColor: 'transparent',
    title: {
      text: '不同算法对比',
      left: 0,
      top: 0,
      textStyle: { color: '#21e6ff', fontSize: 12, fontWeight: 600 },
    },
    legend: {
      top: 0,
      right: 0,
      textStyle: { color: chartTheme.text, fontSize: 11 },
    },
    grid: { left: 48, right: 16, top: 48, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(2, 16, 31, 0.92)',
      borderColor: 'rgba(33, 230, 255, 0.35)',
      textStyle: { color: '#f4fcff' },
    },
    xAxis: {
      type: 'category',
      data: results.map((item) => formatAlgorithmLabel(item.algorithm)),
      axisLine: { lineStyle: { color: chartTheme.axis } },
      axisLabel: { color: chartTheme.text, fontSize: 11 },
    },
    yAxis: [
      {
        type: 'value',
        name: '等待时间(s)',
        nameTextStyle: { color: chartTheme.text, fontSize: 11 },
        axisLabel: { color: chartTheme.text, fontSize: 11 },
        splitLine: { lineStyle: { color: chartTheme.grid } },
      },
      {
        type: 'value',
        name: '通行量',
        nameTextStyle: { color: chartTheme.text, fontSize: 11 },
        axisLabel: { color: chartTheme.text, fontSize: 11 },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '平均等待时间',
        type: 'bar',
        barMaxWidth: 28,
        itemStyle: { color: chartTheme.lineCyan },
        data: results.map((item) => item.avg_waiting_time),
      },
      {
        name: '通行量',
        type: 'bar',
        yAxisIndex: 1,
        barMaxWidth: 28,
        itemStyle: { color: chartTheme.lineYellow },
        data: results.map((item) => item.throughput),
      },
    ],
  })
}

function renderCharts() {
  renderLineCharts()
  renderAlgorithmChart()
}

function resizeCharts() {
  waitingChart?.resize()
  queueChart?.resize()
  throughputChart?.resize()
  algorithmChart?.resize()
}

function disposeCharts() {
  waitingChart?.dispose()
  queueChart?.dispose()
  throughputChart?.dispose()
  algorithmChart?.dispose()
  waitingChart = null
  queueChart = null
  throughputChart = null
  algorithmChart = null
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
  () => [props.timeseries, props.comparison],
  () => {
    renderCharts()
  },
  { deep: true },
)
</script>

<template>
  <section class="metrics-panel">
    <div v-if="!runId" class="empty-state">
      <p>启动仿真后展示实时指标、对比指标与曲线图。</p>
    </div>

    <template v-else>
      <div class="section-head">
        <div>
          <h3 class="section-title">8.1 实时指标</h3>
          <p v-if="realtime" class="section-meta">t = {{ formatSimTime(realtime.time) }}</p>
        </div>
        <el-button size="small" text @click="emit('refresh')">刷新</el-button>
      </div>

      <el-alert
        v-if="realtimeError"
        :title="realtimeError"
        type="error"
        show-icon
        :closable="false"
        class="inline-alert"
      />

      <div v-if="realtimeLoading && !realtime" class="empty-state">
        <el-skeleton animated :rows="2" />
      </div>

      <div v-else class="realtime-grid">
        <OverviewStatCard
          v-for="item in REALTIME_METRIC_ITEMS"
          :key="item.key"
          :label="item.label"
          :value="
            realtime?.metrics[item.key] != null
              ? formatNumber(realtime.metrics[item.key], item.fractionDigits ?? 1)
              : '--'
          "
          :unit="item.unit"
          accent="cyan"
        />
      </div>

      <div class="section-head">
        <div>
          <h3 class="section-title">8.2 对比指标</h3>
          <p class="section-meta">
            固定配时 vs 当前算法（{{ currentAlgorithmLabel }}）
          </p>
        </div>
      </div>

      <el-alert
        v-if="comparisonError"
        :title="comparisonError"
        type="warning"
        show-icon
        :closable="false"
        class="inline-alert"
      />

      <div v-if="comparisonLoading && comparisonRows.length === 0" class="empty-state">
        <el-skeleton animated :rows="3" />
      </div>

      <table v-else-if="comparisonRows.length > 0" class="compare-table">
        <thead>
          <tr>
            <th>指标</th>
            <th>{{ baselineAlgorithmLabel }}</th>
            <th>{{ currentAlgorithmLabel }}</th>
            <th>改善率</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in comparisonRows" :key="row.key">
            <td>{{ row.label }}</td>
            <td>{{ row.baselineDisplay }}</td>
            <td>{{ row.currentDisplay }}</td>
            <td :class="row.improved === true ? 'good' : row.improved === false ? 'bad' : ''">
              {{ row.improvementDisplay }}
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">暂无对比数据</p>

      <div class="section-head">
        <h3 class="section-title">8.3 曲线图</h3>
      </div>

      <el-alert
        v-if="timeseriesError"
        :title="timeseriesError"
        type="warning"
        show-icon
        :closable="false"
        class="inline-alert"
      />

      <div v-if="timeseriesLoading && !timeseries" class="empty-state">
        <el-skeleton animated :rows="4" />
      </div>

      <div v-else class="chart-grid">
        <div ref="waitingChartRef" class="chart-box" />
        <div ref="queueChartRef" class="chart-box" />
        <div ref="throughputChartRef" class="chart-box" />
        <div ref="algorithmChartRef" class="chart-box chart-box-wide" />
      </div>
    </template>
  </section>
</template>

<style scoped>
.metrics-panel {
  display: grid;
  gap: 12px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}

.section-title {
  margin: 0;
  color: #21e6ff;
  font-size: 14px;
  font-weight: 600;
}

.section-meta {
  margin: 4px 0 0;
  color: #78aeca;
  font-size: 12px;
}

.inline-alert {
  margin-bottom: 4px;
}

.empty-state {
  color: #78aeca;
  font-size: 13px;
}

.realtime-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.compare-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.compare-table th,
.compare-table td {
  padding: 8px 6px;
  border-bottom: 1px solid rgba(33, 230, 255, 0.14);
  text-align: left;
}

.compare-table th {
  color: #21e6ff;
  background: rgba(33, 230, 255, 0.08);
}

.compare-table td {
  color: #78aeca;
}

.compare-table .good {
  color: #20f6a4;
}

.compare-table .bad {
  color: #ff4d6d;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.chart-box {
  height: 190px;
  border: 1px solid rgba(33, 230, 255, 0.16);
  border-radius: 7px;
  background: rgba(2, 16, 31, 0.56);
  padding: 8px;
}

.chart-box-wide {
  grid-column: 1 / -1;
  height: 220px;
}

@media (max-width: 1320px) {
  .realtime-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
