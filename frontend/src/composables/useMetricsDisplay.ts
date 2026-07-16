import { computed, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import {
  fetchExperimentComparison,
  fetchMetricsTimeseries,
  fetchRealtimeMetrics,
} from '../api/metrics'
import {
  COMPARISON_POLL_INTERVAL_MS,
  METRICS_POLL_INTERVAL_MS,
} from '../constants/metricsOptions'
import { buildFixedTimeComparisonRows } from '../utils/metricsComparison'
import { createMockMetricsTimeseries } from '../constants/dashboardMockData'
import type {
  ExperimentComparisonResponse,
  MetricComparisonRow,
  MetricsTimeseriesResponse,
  RealtimeMetricsResponse,
} from '../types/metrics'

export function useMetricsDisplay(
  runId: Ref<string>,
  experimentId: Ref<string>,
  currentAlgorithmId: Ref<string>,
) {
  const realtime = ref<RealtimeMetricsResponse | null>(null)
  const comparison = ref<ExperimentComparisonResponse | null>(null)
  const timeseries = ref<MetricsTimeseriesResponse | null>(null)

  const realtimeLoading = ref(false)
  const comparisonLoading = ref(false)
  const timeseriesLoading = ref(false)

  const realtimeError = ref<string | null>(null)
  const comparisonError = ref<string | null>(null)
  const timeseriesError = ref<string | null>(null)

  let metricsTimer: ReturnType<typeof setInterval> | null = null
  let comparisonTimer: ReturnType<typeof setInterval> | null = null

  const comparisonRows = computed<MetricComparisonRow[]>(() => {
    if (!comparison.value) {
      return []
    }

    return buildFixedTimeComparisonRows(
      comparison.value.results,
      currentAlgorithmId.value || comparison.value.results[1]?.algorithm || '',
    )
  })

  async function loadRealtime() {
    if (!runId.value) {
      realtime.value = null
      realtimeError.value = null
      return
    }

    realtimeLoading.value = true
    realtimeError.value = null

    try {
      realtime.value = await fetchRealtimeMetrics(runId.value)
    } catch (err) {
      realtimeError.value = err instanceof Error ? err.message : '加载实时指标失败'
      realtime.value = null
    } finally {
      realtimeLoading.value = false
    }
  }

  async function loadComparison() {
    if (!experimentId.value) {
      comparison.value = null
      comparisonError.value = null
      return
    }

    comparisonLoading.value = true
    comparisonError.value = null

    try {
      comparison.value = await fetchExperimentComparison(experimentId.value)
    } catch (err) {
      comparisonError.value = err instanceof Error ? err.message : '加载对比指标失败'
      comparison.value = null
    } finally {
      comparisonLoading.value = false
    }
  }

  async function loadTimeseries() {
    if (!runId.value) {
      timeseries.value = createMockMetricsTimeseries()
      timeseriesError.value = null
      return
    }

    timeseriesLoading.value = true
    timeseriesError.value = null

    try {
      timeseries.value = await fetchMetricsTimeseries(runId.value)
    } catch {
      timeseries.value = createMockMetricsTimeseries(runId.value)
      timeseriesError.value = null
    } finally {
      timeseriesLoading.value = false
    }
  }

  async function refreshAll() {
    await Promise.all([loadRealtime(), loadComparison(), loadTimeseries()])
  }

  function stopPolling() {
    if (metricsTimer !== null) {
      clearInterval(metricsTimer)
      metricsTimer = null
    }
    if (comparisonTimer !== null) {
      clearInterval(comparisonTimer)
      comparisonTimer = null
    }
  }

  function startPolling() {
    stopPolling()

    void loadTimeseries()

    if (runId.value) {
      void loadRealtime()
      metricsTimer = setInterval(() => {
        void loadRealtime()
        void loadTimeseries()
      }, METRICS_POLL_INTERVAL_MS)
    }

    if (experimentId.value) {
      void loadComparison()
      comparisonTimer = setInterval(() => {
        void loadComparison()
      }, COMPARISON_POLL_INTERVAL_MS)
    }
  }

  onMounted(startPolling)
  onUnmounted(stopPolling)

  watch([runId, experimentId], () => {
    realtime.value = null
    comparison.value = null
    timeseries.value = runId.value ? null : createMockMetricsTimeseries()
    startPolling()
  })

  return {
    realtime,
    comparison,
    timeseries,
    comparisonRows,
    realtimeLoading,
    comparisonLoading,
    timeseriesLoading,
    realtimeError,
    comparisonError,
    timeseriesError,
    refreshAll,
    loadRealtime,
    loadComparison,
    loadTimeseries,
  }
}
