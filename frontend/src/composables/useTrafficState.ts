import { onMounted, ref, watch, type Ref } from 'vue'
import { fetchTrafficState } from '../api/traffic'
import { mergeTrafficState, mergeTrafficSummary } from '../utils/trafficStateMerge'
import type { TrafficStateSnapshot, TrafficSummary } from '../types/traffic'
import { useTrafficWebSocket } from './useTrafficWebSocket'

export function useTrafficState(runId: Ref<string>) {
  const trafficState = ref<TrafficStateSnapshot | null>(null)
  const summary = ref<TrafficSummary>({
    vehicle_count: null,
    avg_speed: null,
  })
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    if (!runId.value) {
      trafficState.value = null
      summary.value = { vehicle_count: null, avg_speed: null }
      error.value = null
      loading.value = false
      return
    }

    loading.value = true
    error.value = null

    try {
      trafficState.value = await fetchTrafficState(runId.value)
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载交通状态失败'
      trafficState.value = null
    } finally {
      loading.value = false
    }
  }

  function applyWsMessage(message: Parameters<typeof mergeTrafficState>[1]) {
    trafficState.value = mergeTrafficState(trafficState.value, message, runId.value)
    summary.value = mergeTrafficSummary(summary.value, message)
  }

  const { connected: wsConnected, error: wsError } = useTrafficWebSocket(runId, applyWsMessage)

  watch(wsError, (message) => {
    if (message && !trafficState.value) {
      error.value = message
    }
  })

  onMounted(() => {
    void load()
  })

  watch(runId, () => {
    summary.value = { vehicle_count: null, avg_speed: null }
    void load()
  })

  return {
    trafficState,
    summary,
    loading,
    error,
    wsConnected,
    refresh: load,
  }
}
