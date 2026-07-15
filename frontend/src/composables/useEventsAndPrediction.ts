import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { fetchRunEvents, fetchRunPrediction } from '../api/events'
import { EVENT_REFRESH_INTERVAL_MS } from '../constants/eventOptions'
import { mergeDetectedEvent } from '../utils/eventMerge'
import {
  connectRunWebSocket,
  registerRunWebSocketConnectionListener,
  registerRunWebSocketHandler,
} from '../utils/runWebSocketManager'
import type {
  EventDetectedWsMessage,
  PredictionResponse,
  TrafficEvent,
} from '../types/events'

function parseEventDetectedMessage(payload: Record<string, unknown>): EventDetectedWsMessage | null {
  if (payload.type !== 'event_detected' || !payload.data || typeof payload.data !== 'object') {
    return null
  }
  return payload as unknown as EventDetectedWsMessage
}

export function useEventsAndPrediction(runId: Ref<string>) {
  const events = ref<TrafficEvent[]>([])
  const prediction = ref<PredictionResponse | null>(null)
  const predictionTarget = ref('J12')
  const predictionHorizon = ref(300)

  const eventsLoading = ref(false)
  const predictionLoading = ref(false)
  const eventsError = ref<string | null>(null)
  const predictionError = ref<string | null>(null)
  const wsConnected = ref(false)

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let unregisterHandler: (() => void) | null = null
  let unregisterConnection: (() => void) | null = null

  const targetOptions = ref<string[]>(['J12'])

  async function loadEvents() {
    if (!runId.value) {
      events.value = []
      eventsError.value = null
      return
    }

    eventsLoading.value = true
    eventsError.value = null

    try {
      const response = await fetchRunEvents(runId.value)
      events.value = response.events.sort((a, b) => b.time - a.time)

      const intersections = new Set<string>(['J12'])
      for (const event of response.events) {
        if (event.location.intersection_id) {
          intersections.add(event.location.intersection_id)
        }
      }
      targetOptions.value = Array.from(intersections)

      if (!intersections.has(predictionTarget.value) && targetOptions.value.length > 0) {
        predictionTarget.value = targetOptions.value[0]
      }
    } catch (err) {
      eventsError.value = err instanceof Error ? err.message : '加载事件列表失败'
      events.value = []
    } finally {
      eventsLoading.value = false
    }
  }

  async function loadPrediction() {
    if (!runId.value || !predictionTarget.value) {
      prediction.value = null
      predictionError.value = null
      return
    }

    predictionLoading.value = true
    predictionError.value = null

    try {
      prediction.value = await fetchRunPrediction(
        runId.value,
        predictionTarget.value,
        predictionHorizon.value,
      )
    } catch (err) {
      predictionError.value = err instanceof Error ? err.message : '加载预测结果失败'
      prediction.value = null
    } finally {
      predictionLoading.value = false
    }
  }

  async function refreshAll() {
    await loadEvents()
    await loadPrediction()
  }

  function setupWebSocket() {
    unregisterHandler?.()
    unregisterConnection?.()

    if (!runId.value) {
      wsConnected.value = false
      return
    }

    connectRunWebSocket(runId.value)

    unregisterHandler = registerRunWebSocketHandler((payload) => {
      const message = parseEventDetectedMessage(payload)
      if (message) {
        events.value = mergeDetectedEvent(events.value, message)
      }
    })

    unregisterConnection = registerRunWebSocketConnectionListener((connected) => {
      wsConnected.value = connected
    })
  }

  function startPolling() {
    stopPolling()
    if (!runId.value) {
      return
    }

    void refreshAll()
    pollTimer = setInterval(() => {
      void refreshAll()
    }, EVENT_REFRESH_INTERVAL_MS)
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  onMounted(() => {
    setupWebSocket()
    startPolling()
  })

  onUnmounted(() => {
    stopPolling()
    unregisterHandler?.()
    unregisterConnection?.()
  })

  watch(runId, () => {
    events.value = []
    prediction.value = null
    setupWebSocket()
    startPolling()
  })

  watch(predictionTarget, () => {
    void loadPrediction()
  })

  return {
    events,
    prediction,
    predictionTarget,
    predictionHorizon,
    targetOptions,
    eventsLoading,
    predictionLoading,
    eventsError,
    predictionError,
    wsConnected,
    refreshAll,
    loadEvents,
    loadPrediction,
  }
}
