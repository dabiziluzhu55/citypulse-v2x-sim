import { computed, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { fetchCollaborationState } from '../api/collaboration'
import {
  formatActionType,
  formatAlgorithm,
  formatPhaseLabel,
  formatStrategy,
} from '../constants/collaborationOptions'
import { mergeCollaborationState } from '../utils/collaborationStateMerge'
import {
  connectRunWebSocket,
  registerRunWebSocketConnectionListener,
  registerRunWebSocketHandler,
} from '../utils/runWebSocketManager'
import { MOCK_COLLABORATION_LOG_ENTRIES } from '../constants/dashboardMockData'
import type {
  CollaborationLogEntry,
  CollaborationStateSnapshot,
  CollaborationStateWsMessage,
} from '../types/collaboration'

const MAX_LOG_ENTRIES = 12

function parseCollaborationMessage(
  payload: Record<string, unknown>,
): CollaborationStateWsMessage | null {
  if (payload.type !== 'collaboration_state' || !payload.data || typeof payload.data !== 'object') {
    return null
  }
  return payload as unknown as CollaborationStateWsMessage
}

function formatTimeLabel(simTime: number): string {
  const minutes = Math.floor(simTime / 60)
  const seconds = Math.floor(simTime % 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function buildLogEntries(state: CollaborationStateSnapshot): CollaborationLogEntry[] {
  const entries: CollaborationLogEntry[] = []
  const timeLabel = formatTimeLabel(state.sim_time)

  if (state.cloud.reason) {
    entries.push({
      id: `cloud-${state.sim_time}`,
      timeLabel,
      source: 'cloud',
      message: `决策：${formatStrategy(state.cloud.strategy)}，${state.cloud.reason}`,
    })
  }

  for (const edge of state.edges.slice(0, 3)) {
    entries.push({
      id: `${edge.edge_agent_id}-${state.sim_time}`,
      timeLabel,
      source: edge.intersection_id,
      message: `上报：${formatPhaseLabel(edge.local_state.current_phase)}，排队 ${edge.local_state.queue_length} 辆，等待 ${edge.local_state.avg_waiting_time.toFixed(1)}s`,
    })

    if (edge.local_rule_check.min_green_satisfied && edge.local_rule_check.conflict_free) {
      entries.push({
        id: `${edge.edge_agent_id}-check-${state.sim_time}`,
        timeLabel,
        source: edge.intersection_id,
        message: '校验：满足最小绿灯约束，允许执行',
      })
    }

    if (edge.last_action.action_type) {
      entries.push({
        id: `${edge.edge_agent_id}-action-${state.sim_time}`,
        timeLabel,
        source: 'SUMO',
        message: `${edge.intersection_id} ${formatActionType(edge.last_action.action_type)} ${edge.last_action.duration}s`,
      })
    }
  }

  for (const vehicle of state.vehicles.slice(0, 2)) {
    const advice =
      vehicle.received_advice.recommended_speed != null
        ? `建议速度 ${vehicle.received_advice.recommended_speed} m/s`
        : vehicle.received_advice.recommended_path ?? '收到协同建议'
    entries.push({
      id: `${vehicle.vehicle_id}-${state.sim_time}`,
      timeLabel,
      source: vehicle.vehicle_id,
      message: `反馈：${advice}，当前 ${vehicle.speed.toFixed(1)} m/s`,
    })
  }

  return entries.slice(0, MAX_LOG_ENTRIES)
}

export function useCollaborationState(runId: Ref<string>) {
  const collaborationState = ref<CollaborationStateSnapshot | null>(null)
  const logEntries = ref<CollaborationLogEntry[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const wsConnected = ref(false)

  const vehicleOnlineCount = computed(
    () => collaborationState.value?.vehicles.length ?? 0,
  )

  const cloudStrategyLabel = computed(() => {
    const strategy = collaborationState.value?.cloud.strategy
    return strategy ? formatStrategy(strategy) : '--'
  })

  function appendLogs(state: CollaborationStateSnapshot) {
    const nextEntries = buildLogEntries(state)
    if (nextEntries.length === 0) {
      return
    }
    logEntries.value = [...nextEntries, ...logEntries.value].slice(0, MAX_LOG_ENTRIES)
  }

  async function load() {
    if (!runId.value) {
      collaborationState.value = null
      logEntries.value = [...MOCK_COLLABORATION_LOG_ENTRIES]
      error.value = null
      loading.value = false
      return
    }

    loading.value = true
    error.value = null

    try {
      const snapshot = await fetchCollaborationState(runId.value)
      collaborationState.value = snapshot
      appendLogs(snapshot)
    } catch {
      collaborationState.value = null
      logEntries.value = [...MOCK_COLLABORATION_LOG_ENTRIES]
      error.value = null
    } finally {
      loading.value = false
    }
  }

  function applyWsMessage(message: CollaborationStateWsMessage) {
    collaborationState.value = mergeCollaborationState(
      collaborationState.value,
      message,
      runId.value,
    )
    if (collaborationState.value) {
      appendLogs(collaborationState.value)
    }
  }

  let unregisterHandler: (() => void) | null = null
  let unregisterConnection: (() => void) | null = null

  function setupWebSocket() {
    unregisterHandler?.()
    unregisterConnection?.()

    if (!runId.value) {
      wsConnected.value = false
      return
    }

    connectRunWebSocket(runId.value)

    unregisterHandler = registerRunWebSocketHandler((payload) => {
      const message = parseCollaborationMessage(payload)
      if (message) {
        applyWsMessage(message)
      }
    })

    unregisterConnection = registerRunWebSocketConnectionListener((connected) => {
      wsConnected.value = connected
    })
  }

  onMounted(() => {
    void load()
    setupWebSocket()
  })

  onUnmounted(() => {
    unregisterHandler?.()
    unregisterConnection?.()
  })

  watch(runId, () => {
    logEntries.value = []
    void load()
    setupWebSocket()
  })

  return {
    collaborationState,
    logEntries,
    loading,
    error,
    wsConnected,
    vehicleOnlineCount,
    cloudStrategyLabel,
    refresh: load,
    formatAlgorithm,
    formatPhaseLabel,
    formatActionType,
    formatStrategy,
  }
}
