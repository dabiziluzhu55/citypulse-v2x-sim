import { computed, ref } from 'vue'
import {
  fetchSimulationStatus,
  startSimulation,
  stopSimulation,
} from '../api/simulation'
import { ACTIVE_SESSION_ID_KEY, STATUS_POLL_INTERVAL_MS } from '../constants/simulationOptions'
import { snapshotToTrafficView } from '../utils/trafficStateMerge'
import {
  connectSimulationStream,
  registerSimulationStreamConnectionListener,
  registerSimulationStreamHandler,
} from '../utils/runWebSocketManager'
import type {
  SimulationSnapshot,
  SimulationState,
  StartSimulationRequest,
  StartSimulationResponse,
  StopSimulationResponse,
} from '../types/simulation'
import { TERMINAL_SIMULATION_STATES } from '../types/simulation'
import type { TrafficSummary } from '../types/traffic'

function isTerminal(state: SimulationState | null | undefined): boolean {
  return !!state && TERMINAL_SIMULATION_STATES.includes(state)
}

const sessionId = ref(localStorage.getItem(ACTIVE_SESSION_ID_KEY) ?? '')
const snapshot = ref<SimulationSnapshot | null>(null)
const starting = ref(false)
const controlling = ref(false)
const startError = ref<string | null>(null)
const controlError = ref<string | null>(null)
const statusError = ref<string | null>(null)
const wsConnected = ref(false)
const lastMessage = ref<string | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null
let requestVersion = 0
let initialized = false

const trafficView = computed(() =>
  snapshot.value ? snapshotToTrafficView(snapshot.value) : null,
)

const summary = computed<TrafficSummary>(() => {
  const metrics = snapshot.value?.metrics
  return {
    vehicle_count: metrics?.active_vehicles ?? null,
    avg_speed: metrics?.mean_speed ?? null,
  }
})

const state = computed<SimulationState | null>(() => snapshot.value?.state ?? null)

function applySnapshot(next: SimulationSnapshot) {
  snapshot.value = next
  statusError.value = null
  if (isTerminal(next.state)) {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollOnce() {
  if (!sessionId.value) {
    snapshot.value = null
    statusError.value = null
    return
  }

  const version = ++requestVersion
  try {
    const next = await fetchSimulationStatus(sessionId.value)
    if (version !== requestVersion) {
      return
    }
    applySnapshot(next)
  } catch (err) {
    if (version !== requestVersion) {
      return
    }
    statusError.value = err instanceof Error ? err.message : '获取仿真状态失败'
  }
}

function startPolling() {
  stopPolling()
  if (!sessionId.value) {
    return
  }
  void pollOnce()
  pollTimer = setInterval(() => {
    if (isTerminal(snapshot.value?.state)) {
      stopPolling()
      return
    }
    void pollOnce()
  }, STATUS_POLL_INTERVAL_MS)
}

function bindSession(nextSessionId: string) {
  sessionId.value = nextSessionId
  if (nextSessionId) {
    localStorage.setItem(ACTIVE_SESSION_ID_KEY, nextSessionId)
  } else {
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
  }
  snapshot.value = null
  statusError.value = null
  connectSimulationStream(nextSessionId)
  startPolling()
}

function ensureInitialized() {
  if (initialized) {
    return
  }
  initialized = true

  registerSimulationStreamHandler((message) => {
    if (message.type === 'snapshot') {
      applySnapshot(message.data)
    }
  })
  registerSimulationStreamConnectionListener((connected) => {
    wsConnected.value = connected
  })

  if (sessionId.value) {
    connectSimulationStream(sessionId.value)
    startPolling()
  }
}

async function launchRun(
  payload: StartSimulationRequest,
): Promise<StartSimulationResponse | null> {
  starting.value = true
  startError.value = null
  try {
    const result = await startSimulation(payload)
    bindSession(result.session_id)
    lastMessage.value = `仿真已启动，状态：${result.state}`
    return result
  } catch (err) {
    startError.value = err instanceof Error ? err.message : '启动仿真失败'
    return null
  } finally {
    starting.value = false
  }
}

async function stopRun(): Promise<StopSimulationResponse | null> {
  if (!sessionId.value) {
    controlError.value = '请先启动仿真'
    return null
  }
  controlling.value = true
  controlError.value = null
  try {
    const result = await stopSimulation(sessionId.value)
    lastMessage.value = `仿真已结束，状态：${result.state}`
    void pollOnce()
    return result
  } catch (err) {
    controlError.value = err instanceof Error ? err.message : '结束仿真失败'
    return null
  } finally {
    controlling.value = false
  }
}

export function useSimulationStore() {
  ensureInitialized()

  return {
    sessionId,
    snapshot,
    trafficView,
    summary,
    state,
    starting,
    controlling,
    startError,
    controlError,
    statusError,
    wsConnected,
    lastMessage,
    launchRun,
    stopRun,
    bindSession,
    refresh: pollOnce,
  }
}
