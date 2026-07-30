import { computed, ref } from 'vue'
import {
  fetchSimulationStatus,
  pauseSimulation,
  resumeSimulation,
  setSimulationPlaybackSpeed,
  startSimulation,
  stopSimulation,
} from '../api/simulation'
import {
  ACTIVE_SESSION_ID_KEY,
  ACTIVE_SIMULATION_CONTEXT_KEY,
  STATUS_POLL_INTERVAL_MS,
} from '../constants/simulationOptions'
import { snapshotToTrafficView } from '../utils/trafficStateMerge'
import { shouldApplySimulationSnapshot } from '../utils/snapshotOrdering'
import {
  appendPlaybackRateSample,
  calculatePlaybackRate,
  type PlaybackRateSample,
} from '../utils/playbackRate'
import {
  connectSimulationStream,
  registerSimulationStreamConnectionListener,
  registerSimulationStreamHandler,
} from '../utils/runWebSocketManager'
import type {
  SimulationPlaybackResponse,
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
  focusIntersectionId: string
  scenarioPresetId: string
  controlMode: string
  playbackSpeed: number
}

function readStoredSimulationContext(): StoredSimulationContext | null {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(ACTIVE_SIMULATION_CONTEXT_KEY) ?? 'null',
    ) as (Partial<StoredSimulationContext> & { intersectionId?: string }) | null
    if (
      parsed
      && typeof parsed.sessionId === 'string'
      && typeof parsed.controlMode === 'string'
    ) {
      return {
        sessionId: parsed.sessionId,
        focusIntersectionId: typeof parsed.focusIntersectionId === 'string'
          ? parsed.focusIntersectionId
          : parsed.intersectionId ?? '',
        scenarioPresetId: typeof parsed.scenarioPresetId === 'string' ? parsed.scenarioPresetId : '',
        controlMode: parsed.controlMode,
        playbackSpeed: typeof parsed.playbackSpeed === 'number' ? parsed.playbackSpeed : 1,
      }
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
  storedContext?.sessionId === sessionId.value ? storedContext.focusIntersectionId : '',
)
const activeScenarioPresetId = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.scenarioPresetId : '',
)
const activeControlMode = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.controlMode : '',
)
const activePlaybackSpeed = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.playbackSpeed : 1,
)
const achievedPlaybackSpeed = ref<number | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null
let requestVersion = 0
let initialized = false
let playbackRateSamples: PlaybackRateSample[] = []

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

function resetPlaybackRateTracking(): void {
  playbackRateSamples = []
  achievedPlaybackSpeed.value = null
}

function recordPlaybackRate(next: SimulationSnapshot): void {
  if (next.state === 'PAUSED') {
    playbackRateSamples = []
    achievedPlaybackSpeed.value = 0
    return
  }
  if (next.state !== 'RUNNING') {
    resetPlaybackRateTracking()
    return
  }
  playbackRateSamples = appendPlaybackRateSample(playbackRateSamples, {
    sessionId: next.session_id,
    wallTimeMs: Date.now(),
    elapsedSeconds: next.elapsed_seconds,
  })
  achievedPlaybackSpeed.value = calculatePlaybackRate(playbackRateSamples)
}

function applySnapshot(next: SimulationSnapshot) {
  if (!shouldApplySimulationSnapshot(snapshot.value, next, sessionId.value)) return
  recordPlaybackRate(next)
  snapshot.value = next
  if (typeof next.playback_speed === 'number') activePlaybackSpeed.value = next.playback_speed
  statusError.value = null
  if (isTerminal(next.state)) {
    stopPolling()
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
    connectSimulationStream('')
    activeScenarioPresetId.value = ''
    activePlaybackSpeed.value = 1
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
    resetPlaybackRateTracking()
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
      resetPlaybackRateTracking()
      sessionIntersectionId.value = ''
      activeScenarioPresetId.value = ''
      activeControlMode.value = ''
      activePlaybackSpeed.value = 1
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
  context?: {
    focusIntersectionId: string
    scenarioPresetId: string
    controlMode: string
    playbackSpeed: number
  },
) {
  sessionId.value = nextSessionId
  restoredSession.value = false
  if (nextSessionId) {
    localStorage.setItem(ACTIVE_SESSION_ID_KEY, nextSessionId)
    if (context) {
      sessionIntersectionId.value = context.focusIntersectionId
      activeScenarioPresetId.value = context.scenarioPresetId
      activeControlMode.value = context.controlMode
      activePlaybackSpeed.value = context.playbackSpeed
      localStorage.setItem(ACTIVE_SIMULATION_CONTEXT_KEY, JSON.stringify({
        sessionId: nextSessionId,
        focusIntersectionId: context.focusIntersectionId,
        scenarioPresetId: context.scenarioPresetId,
        controlMode: context.controlMode,
        playbackSpeed: context.playbackSpeed,
      }))
    }
  } else {
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
    sessionIntersectionId.value = ''
    activeScenarioPresetId.value = ''
    activeControlMode.value = ''
    activePlaybackSpeed.value = 1
  }
  snapshot.value = null
  resetPlaybackRateTracking()
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
  focusIntersectionId: string,
): Promise<StartSimulationResponse | null> {
  starting.value = true
  startError.value = null
  try {
    const result = await startSimulation(payload)
    bindSession(result.session_id, {
      focusIntersectionId,
      scenarioPresetId: result.scenario_preset_id ?? payload.scenario_preset_id,
      controlMode: payload.control_mode,
      playbackSpeed: payload.playback_speed ?? 1,
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

async function runPlaybackControl(
  action: () => Promise<SimulationPlaybackResponse>,
  successMessage: string,
): Promise<SimulationPlaybackResponse | null> {
  if (!sessionId.value) {
    controlError.value = '请先启动仿真'
    return null
  }
  controlling.value = true
  controlError.value = null
  try {
    const result = await action()
    if (typeof result.playback_speed === 'number') activePlaybackSpeed.value = result.playback_speed
    lastMessage.value = `${successMessage}，状态：${result.state}`
    await pollOnce()
    return result
  } catch (err) {
    controlError.value = err instanceof Error ? err.message : `${successMessage}失败`
    return null
  } finally {
    controlling.value = false
  }
}

async function pauseRun() {
  return runPlaybackControl(() => pauseSimulation(sessionId.value), '仿真已暂停')
}

async function resumeRun() {
  return runPlaybackControl(() => resumeSimulation(sessionId.value), '仿真已恢复')
}

async function changePlaybackSpeed(playbackSpeed: number) {
  resetPlaybackRateTracking()
  const result = await runPlaybackControl(
    () => setSimulationPlaybackSpeed(sessionId.value, playbackSpeed),
    `播放倍速已调整为 ${playbackSpeed}×`,
  )
  if (result) {
    activePlaybackSpeed.value = playbackSpeed
    const stored = readStoredSimulationContext()
    if (stored?.sessionId === sessionId.value) {
      localStorage.setItem(ACTIVE_SIMULATION_CONTEXT_KEY, JSON.stringify({
        ...stored,
        playbackSpeed,
      }))
    }
  }
  return result
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
    activeScenarioPresetId,
    activeControlMode,
    activePlaybackSpeed,
    achievedPlaybackSpeed,
    launchRun,
    pauseRun,
    resumeRun,
    changePlaybackSpeed,
    stopRun,
    bindSession,
    markRestoredSessionHandled: () => { restoredSession.value = false },
    refresh: pollOnce,
  }
}
