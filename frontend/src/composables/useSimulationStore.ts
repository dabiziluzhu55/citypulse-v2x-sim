import { computed, ref } from 'vue'
import {
  fetchSimulationStatus,
  startSimulation,
  stopSimulation,
} from '../api/simulation'
import {
  ACTIVE_SESSION_ID_KEY,
  ACTIVE_SIMULATION_CONTEXT_KEY,
  STATUS_POLL_INTERVAL_MS,
} from '../constants/simulationOptions'
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

interface StoredSimulationContext {
  sessionId: string
  intersectionId: string
  controlMode: string
}

function readStoredSimulationContext(): StoredSimulationContext | null {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(ACTIVE_SIMULATION_CONTEXT_KEY) ?? 'null',
    ) as Partial<StoredSimulationContext> | null
    if (
      parsed
      && typeof parsed.sessionId === 'string'
      && typeof parsed.intersectionId === 'string'
      && typeof parsed.controlMode === 'string'
    ) {
      return parsed as StoredSimulationContext
    }
  } catch {
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
  }
  return null
}

function isMissingSessionError(message: string): boolean {
  return /(^|\s)404(\s|$)|not found|不存在|未找到/i.test(message)
}

const sessionId = ref(localStorage.getItem(ACTIVE_SESSION_ID_KEY) ?? '')
const storedContext = readStoredSimulationContext()
const snapshot = ref<SimulationSnapshot | null>(null)
const starting = ref(false)
const controlling = ref(false)
const startError = ref<string | null>(null)
const controlError = ref<string | null>(null)
const statusError = ref<string | null>(null)
const wsConnected = ref(false)
const lastMessage = ref<string | null>(null)
const restoredSession = ref(Boolean(sessionId.value))
const sessionIntersectionId = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.intersectionId : '',
)
const activeControlMode = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.controlMode : '',
)

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
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
    connectSimulationStream('')
    restoredSession.value = false
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
    const message = err instanceof Error ? err.message : '获取仿真状态失败'
    if (restoredSession.value && isMissingSessionError(message)) {
      stopPolling()
      localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
      localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
      connectSimulationStream('')
      sessionId.value = ''
      snapshot.value = null
      sessionIntersectionId.value = ''
      activeControlMode.value = ''
      restoredSession.value = false
      statusError.value = '上次仿真会话已失效，请重新启动仿真'
      return
    }
    statusError.value = message
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

function bindSession(
  nextSessionId: string,
  context?: { intersectionId: string; controlMode: string },
) {
  sessionId.value = nextSessionId
  restoredSession.value = false
  if (nextSessionId) {
    localStorage.setItem(ACTIVE_SESSION_ID_KEY, nextSessionId)
    if (context) {
      sessionIntersectionId.value = context.intersectionId
      activeControlMode.value = context.controlMode
      localStorage.setItem(ACTIVE_SIMULATION_CONTEXT_KEY, JSON.stringify({
        sessionId: nextSessionId,
        intersectionId: context.intersectionId,
        controlMode: context.controlMode,
      }))
    }
  } else {
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
    sessionIntersectionId.value = ''
    activeControlMode.value = ''
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
    if (connected) {
      stopPolling()
    } else if (sessionId.value && !isTerminal(snapshot.value?.state)) {
      startPolling()
    }
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
    bindSession(result.session_id, {
      intersectionId: payload.intersection_ids[0] ?? '',
      controlMode: payload.control_mode,
    })
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
    restoredSession,
    sessionIntersectionId,
    activeControlMode,
    launchRun,
    stopRun,
    bindSession,
    markRestoredSessionHandled: () => { restoredSession.value = false },
    refresh: pollOnce,
  }
}
