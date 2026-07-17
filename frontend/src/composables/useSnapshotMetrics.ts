import { ref, watch, type Ref } from 'vue'
import type { MetricsTimeseriesResponse } from '../types/metrics'
import type { CollaborationLogEntry } from '../types/collaboration'
import type { SimulationSnapshot } from '../types/simulation'

const MAX_POINTS = 120
const MAX_LOG_ENTRIES = 12

function formatClock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds))
  const mm = String(Math.floor(value / 60)).padStart(2, '0')
  const ss = String(value % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

export function useSnapshotMetrics(
  sessionId: Ref<string>,
  snapshot: Ref<SimulationSnapshot | null>,
) {
  const timeseries = ref<MetricsTimeseriesResponse>({ run_id: '', series: [] })
  const logEntries = ref<CollaborationLogEntry[]>([])
  const seenEventStates = new Map<string, string>()

  function reset() {
    timeseries.value = { run_id: sessionId.value, series: [] }
    logEntries.value = []
    seenEventStates.clear()
  }

  watch(sessionId, reset)

  watch(snapshot, (next) => {
    if (!next) {
      return
    }

    const metrics = next.metrics
    if (metrics) {
      const avgWaiting =
        metrics.active_vehicles > 0
          ? metrics.total_waiting_time / metrics.active_vehicles
          : 0
      const point = {
        time: next.elapsed_seconds,
        avg_waiting_time: Number(avgWaiting.toFixed(2)),
        avg_queue_length: metrics.halting_vehicles,
        throughput: metrics.arrived_vehicles,
      }
      const series = [...timeseries.value.series, point]
      timeseries.value = {
        run_id: next.session_id,
        series: series.slice(-MAX_POINTS),
      }
    }

    const timeLabel = formatClock(next.elapsed_seconds)
    const newEntries: CollaborationLogEntry[] = []
    for (const event of next.events ?? []) {
      const key = String(event.event_id ?? event.event_type)
      const currentState = String(event.state ?? 'active')
      if (seenEventStates.get(key) === currentState) {
        continue
      }
      seenEventStates.set(key, currentState)
      newEntries.push({
        id: `${key}-${currentState}-${next.sequence}`,
        timeLabel,
        source: '扰动',
        message: `${event.event_type} · ${currentState}`,
      })
    }
    if (newEntries.length > 0) {
      logEntries.value = [...newEntries, ...logEntries.value].slice(0, MAX_LOG_ENTRIES)
    }
  })

  return {
    timeseries,
    logEntries,
    reset,
  }
}
