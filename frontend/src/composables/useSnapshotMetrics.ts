import { ref, watch, type Ref } from 'vue'
import type { MetricsTimeseriesResponse } from '../types/metrics'
import type { CollaborationLogEntry } from '../types/collaboration'
import type { SimulationSnapshot } from '../types/simulation'
import { simulationFuelIntensity } from '../utils/simulationEvaluation.ts'

const MAX_POINTS = 120
const MAX_LOG_ENTRIES = 180
const SNAPSHOT_LOG_INTERVAL = 25

function formatClock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds))
  const mm = String(Math.floor(value / 60)).padStart(2, '0')
  const ss = String(value % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

function formatWallClock(): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date())
}

function snapshotClock(snapshot: SimulationSnapshot): string {
  const official = snapshot.official_time?.includes('T')
    ? snapshot.official_time.split('T')[1]?.slice(0, 8)
    : snapshot.official_time?.slice(0, 8)
  return /^\d{2}:\d{2}:\d{2}$/.test(official ?? '')
    ? official as string
    : `00:${formatClock(snapshot.elapsed_seconds)}`.slice(-8)
}

export function useSnapshotMetrics(
  sessionId: Ref<string>,
  snapshot: Ref<SimulationSnapshot | null>,
  wsConnected?: Ref<boolean>,
) {
  const timeseries = ref<MetricsTimeseriesResponse>({ run_id: '', series: [] })
  const logEntries = ref<CollaborationLogEntry[]>([])
  const seenEventStates = new Map<string, string>()
  const seenIntersectionPhases = new Map<string, number>()
  let lastLoggedSnapshotSequence = Number.NEGATIVE_INFINITY

  function reset() {
    timeseries.value = { run_id: sessionId.value, series: [] }
    logEntries.value = []
    seenEventStates.clear()
    seenIntersectionPhases.clear()
    lastLoggedSnapshotSequence = Number.NEGATIVE_INFINITY
    if (sessionId.value) {
      logEntries.value = [{
        id: `session-${sessionId.value}`,
        timeLabel: formatWallClock(),
        source: '系统',
        message: `仿真会话 ${sessionId.value.slice(0, 8)} 已建立`,
      }]
    }
  }

  watch(sessionId, reset)

  watch(snapshot, (next) => {
    if (!next) {
      return
    }

    const evaluation = next.evaluation ?? next.metrics.evaluation
    if (evaluation) {
      const fuelIntensity = simulationFuelIntensity(evaluation)
      const point = {
        time: next.elapsed_seconds,
        algorithm: evaluation.algorithm,
        avg_waiting_time: evaluation.avg_waiting_time,
        avg_travel_time: evaluation.avg_travel_time,
        avg_queue_length: evaluation.avg_queue_length,
        throughput: evaluation.throughput,
        fuel_consumption: fuelIntensity,
        fuel_intensity_L_per_100km: fuelIntensity,
        hard_braking_events: evaluation.hard_braking_events ?? null,
        hard_braking_rate: evaluation.hard_braking_rate ?? null,
        finished: evaluation.finished,
        metric_sources: { ...(evaluation.metric_sources ?? {}) },
        warnings: [...(evaluation.warnings ?? [])],
      }
      const series = [...timeseries.value.series, point]
      timeseries.value = {
        run_id: next.session_id,
        series: series.slice(-MAX_POINTS),
      }
    }

    const timeLabel = snapshotClock(next)
    const newEntries: CollaborationLogEntry[] = []
    if (
      !Number.isFinite(lastLoggedSnapshotSequence)
      || next.sequence - lastLoggedSnapshotSequence >= SNAPSHOT_LOG_INTERVAL
    ) {
      lastLoggedSnapshotSequence = next.sequence
      newEntries.push({
        id: `snapshot-${next.session_id}-${next.sequence}`,
        timeLabel,
        source: 'SUMO',
        message: `快照 #${next.sequence}：${next.metrics.active_vehicles} 辆车，平均速度 ${next.metrics.mean_speed.toFixed(1)} m/s`,
      })
    }
    for (const [intersectionId, runtime] of Object.entries(next.intersections)) {
      const previousPhase = seenIntersectionPhases.get(intersectionId)
      if (previousPhase !== undefined && previousPhase !== runtime.current_phase) {
        newEntries.push({
          id: `phase-${intersectionId}-${runtime.current_phase}-${next.sequence}`,
          timeLabel,
          source: '路侧',
          message: `${intersectionId} 信号相位 ${previousPhase} → ${runtime.current_phase}（${runtime.stage}）`,
        })
      }
      seenIntersectionPhases.set(intersectionId, runtime.current_phase)
    }
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

  if (wsConnected) {
    watch(wsConnected, (connected, previous) => {
      if (!sessionId.value || connected === previous) return
      logEntries.value = [{
        id: `connection-${sessionId.value}-${connected}-${Date.now()}`,
        timeLabel: formatWallClock(),
        source: '系统',
        message: connected ? '实时快照通道已连接' : '实时通道已断开，已切换轮询同步',
      }, ...logEntries.value].slice(0, MAX_LOG_ENTRIES)
    })
  }

  return {
    timeseries,
    logEntries,
    reset,
  }
}
