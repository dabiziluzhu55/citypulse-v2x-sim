import { computed, ref } from 'vue'
import {
  fetchSimulationStatus,
  pauseSimulation,
  resumeSimulation,
  setSimulationPlaybackSpeed,
  startSimulation,
  stopSimulation,
} from '../api/simulation'
import { simulationApiErrorMessage } from '../api/client'
import {
  ACTIVE_SESSION_ID_KEY,
  ACTIVE_SIMULATION_CONTEXT_KEY,
  STATUS_POLL_INTERVAL_MS,
} from '../constants/simulationOptions'
import { snapshotToTrafficView } from '../utils/trafficStateMerge'
import { shouldApplySimulationSnapshot } from '../utils/snapshotOrdering'
import { simulationSnapshotErrorMessage } from '../utils/simulationSessionError.ts'
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
import {
  ConfirmedSimulationClock,
  formatOfficialTimeSeconds,
} from '../utils/confirmedSimulationClock'
import {
  recordSimulationDiagnosticSnapshot,
  recordSimulationDiagnosticConnection,
  resetSimulationRuntimeDiagnostics,
} from '../utils/simulationRuntimeDiagnostics'
import {
  DISTURBANCE_RUNTIME_STORAGE_KEY,
  freezeDisturbanceRuntimeTargets,
  parseStoredDisturbanceRuntimeTargets,
  runtimeDisturbanceViews,
  type DisturbanceRuntimeTarget,
} from '../utils/runtimeDisturbances'

function isTerminal(state: SimulationState | null | undefined): boolean {
  return !!state && TERMINAL_SIMULATION_STATES.includes(state)
}

interface StoredSimulationContext {
  sessionId: string
  focusIntersectionId: string
  scenarioPresetId: string
  controlMode: string
  playbackSpeed: number
  websocketUrl: string
  period: string
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
        websocketUrl: typeof parsed.websocketUrl === 'string' ? parsed.websocketUrl : '',
        period: typeof parsed.period === 'string' ? parsed.period : '',
      }
    }
  } catch {
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
  }
  return null
}

function isMissingSessionError(message: string): boolean {
  return /(^|\s)404(\s|$)|not found|unknown session|不存在|未找到/i.test(message)
}

const sessionId = ref(localStorage.getItem(ACTIVE_SESSION_ID_KEY) ?? '')
const storedContext = readStoredSimulationContext()
const snapshot = ref<SimulationSnapshot | null>(null)
const storedRuntimeTargets = parseStoredDisturbanceRuntimeTargets(
  localStorage.getItem(DISTURBANCE_RUNTIME_STORAGE_KEY),
)
const runtimeDisturbanceTargets = ref<DisturbanceRuntimeTarget[]>(
  storedRuntimeTargets?.sessionId === sessionId.value ? storedRuntimeTargets.targets : [],
)
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
const activeWebsocketUrl = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.websocketUrl : '',
)
const activeSimulationPeriod = ref(
  storedContext?.sessionId === sessionId.value ? storedContext.period : '',
)
const achievedPlaybackSpeed = ref<number | null>(null)
const acceptedState = ref<SimulationState | null>(null)
const displayedOfficialTime = ref('')
// Changes only after the backend accepts a different session. Renderers use
// this boundary to discard vehicles and interpolation state from the old run.
const renderSessionRevision = ref(0)

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollAbortController: AbortController | null = null
let requestVersion = 0
let initialized = false
let playbackRateSamples: PlaybackRateSample[] = []
let clockTimer: ReturnType<typeof setInterval> | null = null
const confirmedClock = new ConfirmedSimulationClock()

const trafficView = computed(() =>
  snapshot.value ? snapshotToTrafficView(snapshot.value) : null,
)
const runtimeDisturbances = computed(() => (
  runtimeDisturbanceViews(runtimeDisturbanceTargets.value, snapshot.value)
))
const unmappedRuntimeEvents = computed(() => {
  const mapped = new Set(runtimeDisturbances.value.map((target) => target.eventId))
  return (snapshot.value?.events ?? []).filter((event) => !mapped.has(event.event_id))
})

function setRuntimeDisturbanceTargets(
  nextSessionId: string,
  payload: StartSimulationRequest,
): void {
  runtimeDisturbanceTargets.value = freezeDisturbanceRuntimeTargets(
    nextSessionId,
    payload.disturbance_targets,
  )
  localStorage.setItem(DISTURBANCE_RUNTIME_STORAGE_KEY, JSON.stringify({
    version: 1,
    sessionId: nextSessionId,
    targets: runtimeDisturbanceTargets.value,
  }))
}

function clearPersistedRuntimeDisturbances(clearMemory = true): void {
  localStorage.removeItem(DISTURBANCE_RUNTIME_STORAGE_KEY)
  if (clearMemory) runtimeDisturbanceTargets.value = []
}

const summary = computed<TrafficSummary>(() => {
  const metrics = snapshot.value?.metrics
  return {
    vehicle_count: metrics?.active_vehicles ?? null,
    avg_speed: metrics?.mean_speed ?? null,
  }
})

const state = computed<SimulationState | null>(() => snapshot.value?.state ?? acceptedState.value)

function resetPlaybackRateTracking(): void {
  playbackRateSamples = []
  achievedPlaybackSpeed.value = null
}

function resetDisplayedClock(): void {
  confirmedClock.reset()
  displayedOfficialTime.value = ''
}

function tickDisplayedClock(): void {
  const current = snapshot.value
  if (!current) return
  const value = confirmedClock.valueAt(Date.now())
  if (value != null) displayedOfficialTime.value = formatOfficialTimeSeconds(value)
  else displayedOfficialTime.value = current.official_time
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
  confirmedClock.accept({
    sequence: next.sequence,
    officialTime: next.official_time,
    state: next.state,
    arrivalTimeMs: Date.now(),
  })
  recordSimulationDiagnosticSnapshot(next, achievedPlaybackSpeed.value)
  const previousState = state.value
  snapshot.value = next
  tickDisplayedClock()
  acceptedState.value = next.state
  if (typeof next.playback_speed === 'number') activePlaybackSpeed.value = next.playback_speed
  const snapshotFailure = simulationSnapshotErrorMessage(next)
  if (next.state === 'FAILED' && !next.error && statusError.value) {
    // Keep the detailed error from an earlier terminal snapshot.
  } else if (snapshotFailure) statusError.value = snapshotFailure
  else statusError.value = null
  if (isTerminal(next.state)) {
    stopPolling()
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
    clearPersistedRuntimeDisturbances(false)
    connectSimulationStream('')
    activeScenarioPresetId.value = ''
    activePlaybackSpeed.value = 1
    activeWebsocketUrl.value = ''
    activeSimulationPeriod.value = ''
    restoredSession.value = false
  } else if (previousState === 'QUEUED' && next.state !== 'QUEUED') {
    lastMessage.value = '已获得仿真资源，正在启动仿真'
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  requestVersion += 1
  pollAbortController?.abort()
  pollAbortController = null
}

async function pollOnce() {
  if (!sessionId.value) {
    snapshot.value = null
    resetPlaybackRateTracking()
    statusError.value = null
    return
  }

  const version = ++requestVersion
  pollAbortController?.abort()
  const abortController = new AbortController()
  pollAbortController = abortController
  try {
    const next = await fetchSimulationStatus(sessionId.value, abortController.signal)
    if (version !== requestVersion) {
      return
    }
    applySnapshot(next)
  } catch (err) {
    if (version !== requestVersion) {
      return
    }
    const message = err instanceof Error ? err.message : '获取仿真状态失败'
    if (isMissingSessionError(message)) {
      stopPolling()
      localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
      localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
      clearPersistedRuntimeDisturbances()
      connectSimulationStream('')
      sessionId.value = ''
      snapshot.value = null
      acceptedState.value = null
      resetPlaybackRateTracking()
      sessionIntersectionId.value = ''
      activeScenarioPresetId.value = ''
      activeControlMode.value = ''
      activePlaybackSpeed.value = 1
      activeWebsocketUrl.value = ''
      activeSimulationPeriod.value = ''
      restoredSession.value = false
      statusError.value = '仿真会话已失效，请重新启动仿真'
      return
    }
    statusError.value = message
  } finally {
    if (pollAbortController === abortController) pollAbortController = null
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
    websocketUrl: string
    period: string
    initialState?: SimulationState
  },
) {
  const runtimeSessionId = runtimeDisturbanceTargets.value[0]?.sessionId ?? ''
  if (nextSessionId && runtimeSessionId && runtimeSessionId !== nextSessionId) {
    clearPersistedRuntimeDisturbances()
  }
  if (nextSessionId && nextSessionId !== sessionId.value) {
    renderSessionRevision.value += 1
  }
  sessionId.value = nextSessionId
  acceptedState.value = context?.initialState ?? null
  restoredSession.value = false
  if (nextSessionId) {
    localStorage.setItem(ACTIVE_SESSION_ID_KEY, nextSessionId)
    if (context) {
      sessionIntersectionId.value = context.focusIntersectionId
      activeScenarioPresetId.value = context.scenarioPresetId
      activeControlMode.value = context.controlMode
      activePlaybackSpeed.value = context.playbackSpeed
      activeWebsocketUrl.value = context.websocketUrl
      activeSimulationPeriod.value = context.period
      localStorage.setItem(ACTIVE_SIMULATION_CONTEXT_KEY, JSON.stringify({
        sessionId: nextSessionId,
        focusIntersectionId: context.focusIntersectionId,
        scenarioPresetId: context.scenarioPresetId,
        controlMode: context.controlMode,
        playbackSpeed: context.playbackSpeed,
        websocketUrl: context.websocketUrl,
        period: context.period,
      }))
    }
  } else {
    localStorage.removeItem(ACTIVE_SESSION_ID_KEY)
    localStorage.removeItem(ACTIVE_SIMULATION_CONTEXT_KEY)
    clearPersistedRuntimeDisturbances()
    sessionIntersectionId.value = ''
    activeScenarioPresetId.value = ''
    activeControlMode.value = ''
    activePlaybackSpeed.value = 1
    activeWebsocketUrl.value = ''
    activeSimulationPeriod.value = ''
    acceptedState.value = null
  }
  snapshot.value = null
  resetPlaybackRateTracking()
  resetDisplayedClock()
  resetSimulationRuntimeDiagnostics(nextSessionId)
  statusError.value = null
  connectSimulationStream(nextSessionId, activeWebsocketUrl.value)
  startPolling()
}

function ensureInitialized() {
  if (initialized) {
    return
  }
  initialized = true
  if (clockTimer === null) clockTimer = setInterval(tickDisplayedClock, 100)

  registerSimulationStreamHandler((message) => {
    if (message.type === 'snapshot') {
      applySnapshot(message.data)
    }
  })
  registerSimulationStreamConnectionListener((connected) => {
    wsConnected.value = connected
    recordSimulationDiagnosticConnection(connected)
    if (connected) {
      stopPolling()
    } else if (sessionId.value && !isTerminal(snapshot.value?.state)) {
      startPolling()
    }
  })

  if (sessionId.value) {
    connectSimulationStream(sessionId.value, activeWebsocketUrl.value)
    startPolling()
  }
}

async function launchRun(
  payload: StartSimulationRequest,
  focusIntersectionId: string,
  onSessionAccepted?: (result: StartSimulationResponse) => void,
): Promise<StartSimulationResponse | null> {
  starting.value = true
  startError.value = null
  try {
    const result = await startSimulation(payload)
    setRuntimeDisturbanceTargets(result.session_id, payload)
    onSessionAccepted?.(result)
    bindSession(result.session_id, {
      focusIntersectionId,
      scenarioPresetId: result.scenario_preset_id ?? payload.scenario_preset_id,
      controlMode: payload.control_mode,
      playbackSpeed: payload.playback_speed ?? 1,
      websocketUrl: result.websocket_url,
      period: payload.period,
      initialState: result.state,
    })
    lastMessage.value = result.state === 'QUEUED'
      ? '排队中，等待仿真资源'
      : `仿真已启动，状态：${result.state}`
    return result
  } catch (err) {
    startError.value = simulationApiErrorMessage(err, '启动仿真失败')
    return null
  } finally {
    starting.value = false
  }
}

function clearStatusError(): void {
  statusError.value = null
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
    controlError.value = simulationApiErrorMessage(err, `${successMessage}失败`)
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
    controlError.value = simulationApiErrorMessage(err, '结束仿真失败')
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
    clearStatusError,
    wsConnected,
    lastMessage,
    restoredSession,
    sessionIntersectionId,
    activeScenarioPresetId,
    activeControlMode,
    activePlaybackSpeed,
    activeSimulationPeriod,
    achievedPlaybackSpeed,
    displayedOfficialTime,
    renderSessionRevision,
    runtimeDisturbanceTargets,
    runtimeDisturbances,
    unmappedRuntimeEvents,
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
