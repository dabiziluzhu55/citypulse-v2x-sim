import { ref, watch, type Ref } from 'vue'
import type { MetricsTimeseriesResponse } from '../types/metrics'
import type {
  CollaborationLogEntry,
  V2XLinkType,
  V2XLogStatus,
  V2XRole,
} from '../types/collaboration'
import type { SimulationSnapshot, V2XEventState } from '../types/simulation'
import { formatIntersectionReferences } from '../utils/intersectionLabels.ts'
import { simulationFuelIntensity } from '../utils/simulationEvaluation.ts'

const MAX_POINTS = 120
const MAX_LOG_ENTRIES = 180
const MAX_SEEN_V2X_EVENTS = 2_000

const V2X_MESSAGE_META: Record<string, { tag: string; title: string }> = {
  VehicleStateV1: { tag: 'CV Status', title: '车辆状态上报' },
  IntersectionSummaryV1: { tag: 'MAP Update', title: '路口状态汇总上传' },
  RegionalPriorityV1: { tag: 'Coordination', title: '协调策略下发' },
  SPaTV2: { tag: 'SPaT', title: '信号相位与配时广播' },
  MAPV1: { tag: 'MAP', title: '路口拓扑信息广播' },
}

function resolveLinkType(source: V2XRole, destination: V2XRole): V2XLinkType {
  const links: Record<string, V2XLinkType> = {
    'vehicle->road': 'V2I',
    'vehicle->cloud': 'V2I',
    'road->vehicle': 'I2V',
    'cloud->vehicle': 'I2V',
    'road->cloud': 'I2C',
    'cloud->road': 'C2I',
    'vehicle->vehicle': 'V2V',
    'cloud->cloud': 'C2C',
  }
  return links[`${source}->${destination}`] ?? 'UNKNOWN'
}

function resolveStatus(event: V2XEventState): V2XLogStatus {
  if (event === 'TTL_EXPIRED') return 'failed'
  if (event === 'DELIVER' || event === 'CONSUME') return 'success'
  return 'sending'
}

function parseClockSeconds(value: string): number {
  const [hour = 0, minute = 0, second = 0] = value.split(':').map(Number)
  return hour * 3_600 + minute * 60 + second
}

function formatCommunicationClock(
  eventSeconds: number,
  officialTime: string,
  elapsedSeconds: number,
): string {
  const startSeconds = parseClockSeconds(officialTime) - elapsedSeconds
  const normalized = ((startSeconds + eventSeconds) % 86_400 + 86_400) % 86_400
  const wholeSeconds = Math.floor(normalized)
  const milliseconds = Math.round((normalized - wholeSeconds) * 1_000)
  const hour = Math.floor(wholeSeconds / 3_600)
  const minute = Math.floor((wholeSeconds % 3_600) / 60)
  const second = wholeSeconds % 60
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}.${String(milliseconds).padStart(3, '0')}`
}

export function useSnapshotMetrics(
  sessionId: Ref<string>,
  snapshot: Ref<SimulationSnapshot | null>,
  _wsConnected?: Ref<boolean>,
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

    for (const event of next.v2x_events ?? []) {
      const key = `${event.sequence}:${event.event}:${event.message_id}`
      if (seenV2xEventKeys.has(key)) continue
      seenV2xEventKeys.add(key)
      seenV2xEventOrder.push(key)
      if (seenV2xEventOrder.length > MAX_SEEN_V2X_EVENTS) {
        const expiredKey = seenV2xEventOrder.shift()
        if (expiredKey) seenV2xEventKeys.delete(expiredKey)
      }
      const meta = V2X_MESSAGE_META[event.message_type] ?? {
        tag: event.message_type,
        title: event.message_type,
      }
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
        source: formatIntersectionReferences(event.source_id),
        sourceRole: event.source_role,
        destination: formatIntersectionReferences(event.destination_id),
        destinationRole: event.destination_role,
        linkType: resolveLinkType(event.source_role, event.destination_role),
        eventState: isNewerLifecycle ? event.event : existing?.eventState,
        messageType: event.message_type,
        messageTag: meta.tag,
        message: formatIntersectionReferences(meta.title),
        detail: undefined,
        latencyMs: Math.max(
          existing?.latencyMs ?? 0,
          Math.round((event.message_age_s ?? 0) * 1_000),
        ),
        status: isNewerLifecycle ? resolveStatus(event.event) : existing?.status,
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
