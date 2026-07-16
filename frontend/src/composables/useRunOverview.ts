import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { fetchRunOverview } from '../api/overview'
import {
  connectRunWebSocket,
  getCurrentRunWebSocketId,
  registerRunWebSocketConnectionListener,
  registerRunWebSocketHandler,
} from '../utils/runWebSocketManager'
import type { RunOverview } from '../types/overview'

export type OverviewUpdateSource = 'poll' | 'ws'

const POLL_INTERVAL_MS = 5_000

function isOverview(payload: Record<string, unknown>): payload is Record<string, unknown> & RunOverview {
  return typeof payload.run_id === 'string'
    && typeof payload.scenario_id === 'string'
    && typeof payload.sim_time === 'number'
    && typeof payload.vehicle_count === 'number'
}

export function useRunOverview(runId: Ref<string>) {
  const overview = ref<RunOverview | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const wsConnected = ref(false)
  const lastSource = ref<OverviewUpdateSource | null>(null)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function load() {
    if (!runId.value) {
      overview.value = null
      error.value = null
      loading.value = false
      return
    }

    loading.value = overview.value === null
    try {
      overview.value = await fetchRunOverview(runId.value)
      lastSource.value = 'poll'
      error.value = null
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载系统总览失败'
    } finally {
      loading.value = false
    }
  }

  function restartPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
    }
    pollTimer = setInterval(() => {
      void load()
    }, POLL_INTERVAL_MS)
  }

  const unregisterMessage = registerRunWebSocketHandler((payload) => {
    const candidate = payload.type === 'overview' && typeof payload.data === 'object'
      ? payload.data as Record<string, unknown>
      : payload
    if (isOverview(candidate) && candidate.run_id === runId.value) {
      overview.value = candidate
      lastSource.value = 'ws'
      error.value = null
    }
  })

  const unregisterConnection = registerRunWebSocketConnectionListener((connected) => {
    wsConnected.value = connected && getCurrentRunWebSocketId() === runId.value
  })

  onMounted(() => {
    connectRunWebSocket(runId.value)
    void load()
    restartPolling()
  })

  watch(runId, (nextRunId) => {
    overview.value = null
    lastSource.value = null
    connectRunWebSocket(nextRunId)
    void load()
  })

  onUnmounted(() => {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
    }
    unregisterMessage()
    unregisterConnection()
  })

  return {
    overview,
    loading,
    error,
    wsConnected,
    lastSource,
    refresh: load,
  }
}
