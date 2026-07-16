import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import type { TrafficStateWsMessage } from '../types/traffic'
import {
  connectRunWebSocket,
  registerRunWebSocketConnectionListener,
  registerRunWebSocketHandler,
} from '../utils/runWebSocketManager'

function parseTrafficMessage(payload: Record<string, unknown>): TrafficStateWsMessage | null {
  if (payload.type !== 'traffic_state' || !payload.data || typeof payload.data !== 'object') {
    return null
  }
  return payload as unknown as TrafficStateWsMessage
}

export function useTrafficWebSocket(
  runId: Ref<string>,
  onUpdate: (message: TrafficStateWsMessage) => void,
) {
  const connected = ref(false)
  const error = ref<string | null>(null)

  let unregisterHandler: (() => void) | null = null
  let unregisterConnection: (() => void) | null = null

  function setup() {
    unregisterHandler?.()
    unregisterConnection?.()

    if (!runId.value) {
      connected.value = false
      return
    }

    connectRunWebSocket(runId.value)

    unregisterHandler = registerRunWebSocketHandler((payload) => {
      const message = parseTrafficMessage(payload)
      if (message) {
        onUpdate(message)
      }
    })

    unregisterConnection = registerRunWebSocketConnectionListener((isConnected) => {
      connected.value = isConnected
      if (isConnected) {
        error.value = null
      }
    })
  }

  onMounted(setup)

  onUnmounted(() => {
    unregisterHandler?.()
    unregisterConnection?.()
  })

  watch(runId, setup)

  return {
    connected,
    error,
  }
}
