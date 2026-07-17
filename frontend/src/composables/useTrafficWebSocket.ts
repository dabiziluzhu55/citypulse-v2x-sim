import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import type { SimulationSnapshot } from '../types/simulation'
import {
  connectSimulationStream,
  registerSimulationStreamConnectionListener,
  registerSimulationStreamHandler,
} from '../utils/runWebSocketManager'

export function useSimulationStream(
  sessionId: Ref<string>,
  onSnapshot: (snapshot: SimulationSnapshot) => void,
) {
  const connected = ref(false)
  const error = ref<string | null>(null)

  let unregisterHandler: (() => void) | null = null
  let unregisterConnection: (() => void) | null = null

  function setup() {
    unregisterHandler?.()
    unregisterConnection?.()

    if (!sessionId.value) {
      connected.value = false
      connectSimulationStream('')
      return
    }

    connectSimulationStream(sessionId.value)

    unregisterHandler = registerSimulationStreamHandler((message) => {
      if (message.type === 'snapshot') {
        onSnapshot(message.data)
      }
    })

    unregisterConnection = registerSimulationStreamConnectionListener((isConnected) => {
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

  watch(sessionId, setup)

  return {
    connected,
    error,
  }
}
