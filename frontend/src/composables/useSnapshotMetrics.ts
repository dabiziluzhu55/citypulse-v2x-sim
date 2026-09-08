import { ref, watch, type Ref } from 'vue'
import type { MetricsTimeseriesResponse } from '../types/metrics'
import type { CollaborationLogEntry } from '../types/collaboration'
import type { SimulationSnapshot } from '../types/simulation'
import { simulationFuelIntensity } from '../utils/simulationEvaluation.ts'
import {
  formatCommunicationClock,
  formatV2XEndpoint,
  isCov2xControlMode,
  latencyMsFromMessageAge,
  resolveV2XLinkType,
  resolveV2XMessageMeta,
  resolveV2XStatus,
} from '../utils/v2xCommunication.ts'

const MAX_POINTS = 120
const MAX_LOG_ENTRIES = 180
const MAX_SEEN_V2X_EVENTS = 2_000

function snapshotControlMode(
  snapshot: SimulationSnapshot,
  fallback?: string,
): string {
  return (
    snapshot.evaluation?.algorithm
    ?? snapshot.metrics.evaluation?.algorithm
    ?? fallback
    ?? ''
  )
}

export function useSnapshotMetrics(
  sessionId: Ref<string>,
  snapshot: Ref<SimulationSnapshot | null>,
  _wsConnected?: Ref<boolean>,
  controlMode?: Ref<string>,
) {
  const timeseries = ref<MetricsTimeseriesResponse>({ run_id: '', series: [] })
  const logEntries = ref<CollaborationLogEntry[]>([])
  const seenV2xEventKeys = new Set<string>()
  const seenV2xEventOrder: string[] = []
  const messagesById = new Map<string, CollaborationLogEntry>()

  function reset() {
    timeseries.value = { run_id: sessionId.value, series: [] }
    logEntries.value = []
    seenV2xEventKeys.clear()
    seenV2xEventOrder.length = 0
    messagesById.clear()
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
        path_avg_speed_kmh: evaluation.path_avg_speed_kmh ?? null,
        travel_time_index: evaluation.travel_time_index ?? null,
        delay_time_proportion: evaluation.delay_time_proportion ?? null,
        traffic_performance_index: evaluation.traffic_performance_index ?? null,
        traffic_state: evaluation.traffic_state ?? null,
        tpi_method: evaluation.tpi_method ?? null,
        avg_stops_per_vehicle: evaluation.avg_stops_per_vehicle ?? null,
        regional_max_queue_length_m: evaluation.regional_max_queue_length_m ?? null,
        regional_max_queue_intersection_id: evaluation.regional_max_queue_intersection_id ?? null,
        regional_max_queue_lane_id: evaluation.regional_max_queue_lane_id ?? null,
        regional_max_queue_sim_time_s: evaluation.regional_max_queue_sim_time_s ?? null,
        spillback_rate: evaluation.spillback_rate ?? null,
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

    if (!isCov2xControlMode(snapshotControlMode(next, controlMode?.value))) {
      logEntries.value = []
      seenV2xEventKeys.clear()
      seenV2xEventOrder.length = 0
      messagesById.clear()
      return
    }

    for (const event of next.v2x_events ?? []) {
      const key = `${event.sequence}:${event.event}:${event.message_id}`
      if (seenV2xEventKeys.has(key)) continue
      seenV2xEventKeys.add(key)
      seenV2xEventOrder.push(key)
      if (seenV2xEventOrder.length > MAX_SEEN_V2X_EVENTS) {
        const expiredKey = seenV2xEventOrder.shift()
        if (expiredKey) seenV2xEventKeys.delete(expiredKey)
      }
      const meta = resolveV2XMessageMeta(event.message_type, event.destination_role)
      const existing = messagesById.get(event.message_id)
      const isNewerLifecycle = existing?.sequence == null || event.sequence >= existing.sequence
      const entry: CollaborationLogEntry = {
        id: `v2x-${event.message_id}`,
        messageId: event.message_id,
        sequence: Math.max(existing?.sequence ?? 0, event.sequence),
        dateLabel: new Date().toLocaleDateString('sv-SE'),
        timeLabel: formatCommunicationClock(
          event.event_time_s,
          next.official_time,
          next.elapsed_seconds,
        ),
        eventTimeSeconds: event.event_time_s,
        source: formatV2XEndpoint(event.source_role, event.source_id),
        sourceRole: event.source_role,
        destination: formatV2XEndpoint(event.destination_role, event.destination_id),
        destinationRole: event.destination_role,
        linkType: resolveV2XLinkType(event.source_role, event.destination_role),
        eventState: isNewerLifecycle ? event.event : existing?.eventState,
        messageType: event.message_type,
        messageTag: meta.tag,
        message: meta.title,
        detail: undefined,
        latencyMs: isNewerLifecycle
          ? latencyMsFromMessageAge(event.message_age_s)
          : existing?.latencyMs,
        status: isNewerLifecycle ? resolveV2XStatus(event.event) : existing?.status,
        causalParentIds: isNewerLifecycle
          ? [...(event.causal_parent_ids ?? [])]
          : existing?.causalParentIds,
      }
      messagesById.set(event.message_id, entry)
    }
    logEntries.value = [...messagesById.values()]
      .sort((left, right) => (right.sequence ?? 0) - (left.sequence ?? 0))
      .slice(0, MAX_LOG_ENTRIES)
  })

  return {
    timeseries,
    logEntries,
    reset,
  }
}
