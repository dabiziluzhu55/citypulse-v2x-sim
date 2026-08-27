import type { SimulationWsMessage } from '../types/simulation'
import { recordSnapshotDecodeDiagnostics } from './simulationRuntimeDiagnostics.ts'

type MessageHandler = (message: SimulationWsMessage) => void

let socket: WebSocket | null = null
let currentSessionId = ''
let currentStreamUrl = ''
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
const MAX_RECONNECT_DELAY_MS = 30_000
const STREAM_STALE_TIMEOUT_MS = 7_000
let shouldReconnect = false
let lastStreamMessageAt = 0
let streamWatchdog: ReturnType<typeof setInterval> | null = null
let decoderWorker: Worker | null = null
let decoderWorkerUnavailable = false
let decodeGeneration = 0
const handlers = new Set<MessageHandler>()
const connectionListeners = new Set<(connected: boolean) => void>()

interface StreamLocation {
  protocol: string
  host: string
  origin: string
}

export function resolveSimulationStreamUrl(
  sessionId: string,
  backendStreamUrl = '',
  location: StreamLocation = window.location,
): string {
  const configuredUrl = import.meta.env?.VITE_TRAFFIC_WS_URL?.trim() ?? ''
  const candidate = (backendStreamUrl.trim() || configuredUrl)
    .replace('{session_id}', encodeURIComponent(sessionId))
  if (candidate) {
    const url = new URL(candidate, location.origin)
    if (url.protocol === 'http:') url.protocol = 'ws:'
    if (url.protocol === 'https:') url.protocol = 'wss:'
    return url.toString()
  }

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = location.host
  return `${protocol}//${host}/api/v1/simulations/${encodeURIComponent(sessionId)}/stream`
}

function notifyConnection(connected: boolean) {
  for (const listener of connectionListeners) {
    listener(connected)
  }
}

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function stopStreamWatchdog() {
  if (streamWatchdog !== null) {
    clearInterval(streamWatchdog)
    streamWatchdog = null
  }
}

function startStreamWatchdog() {
  stopStreamWatchdog()
  streamWatchdog = setInterval(() => {
    if (
      socket?.readyState === WebSocket.OPEN
      && Date.now() - lastStreamMessageAt > STREAM_STALE_TIMEOUT_MS
    ) {
      socket.close(4000, 'simulation stream stale')
    }
  }, 1_000)
}

function closeSocket() {
  stopStreamWatchdog()
  if (socket) {
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    socket.close()
    socket = null
  }
}

function scheduleReconnect() {
  if (!shouldReconnect || !currentSessionId) {
    return
  }

  clearReconnectTimer()
  reconnectAttempts += 1
  const delay = Math.min(3_000 * 2 ** (reconnectAttempts - 1), MAX_RECONNECT_DELAY_MS)
  reconnectTimer = setTimeout(() => {
    connectSimulationStream(currentSessionId, currentStreamUrl)
  }, delay)
}

function isSimulationMessage(payload: unknown): payload is SimulationWsMessage {
  if (!payload || typeof payload !== 'object') {
    return false
  }
  const type = (payload as { type?: unknown }).type
  return type === 'snapshot' || type === 'heartbeat'
}

function dispatchMessage(message: SimulationWsMessage): void {
  for (const handler of handlers) handler(message)
}

function ensureDecoderWorker(): Worker | null {
  if (decoderWorker) return decoderWorker
  if (decoderWorkerUnavailable || typeof Worker === 'undefined') return null
  try {
    decoderWorker = new Worker(
      new URL('../workers/simulationSnapshotDecoder.worker.ts', import.meta.url),
      { type: 'module' },
    )
    decoderWorker.onmessage = (event: MessageEvent<{
      generation: number
      messages: SimulationWsMessage[]
      parseDurationMs: number
      coalescedSnapshotCount: number
    }>) => {
      if (event.data.generation !== decodeGeneration) return
      recordSnapshotDecodeDiagnostics(
        event.data.parseDurationMs,
        event.data.coalescedSnapshotCount,
      )
      for (const message of event.data.messages) dispatchMessage(message)
    }
    decoderWorker.onerror = () => {
      decoderWorker?.terminate()
      decoderWorker = null
      decoderWorkerUnavailable = true
    }
    return decoderWorker
  } catch {
    decoderWorker = null
    decoderWorkerUnavailable = true
    return null
  }
}

function decodeMessage(raw: string, expectedSessionId: string, generation: number): void {
  const worker = ensureDecoderWorker()
  if (worker) {
    worker.postMessage({ raw, expectedSessionId, generation })
    return
  }
  const startedAt = performance.now()
  try {
    const payload = JSON.parse(raw) as unknown
    recordSnapshotDecodeDiagnostics(performance.now() - startedAt, 0)
    if (isSimulationMessage(payload)) dispatchMessage(payload)
  } catch {
    // Malformed stream messages do not interrupt the active session.
  }
}

export function connectSimulationStream(sessionId: string, backendStreamUrl = '') {
  if (!sessionId) {
    decodeGeneration += 1
    shouldReconnect = false
    clearReconnectTimer()
    closeSocket()
    currentSessionId = ''
    currentStreamUrl = ''
    notifyConnection(false)
    return
  }

  const nextStreamUrl = backendStreamUrl.trim()
    || (sessionId === currentSessionId ? currentStreamUrl : '')
  if (
    sessionId === currentSessionId
    && nextStreamUrl === currentStreamUrl
    && socket
    && socket.readyState === WebSocket.OPEN
  ) {
    return
  }

  const generation = ++decodeGeneration

  shouldReconnect = true
  currentSessionId = sessionId
  currentStreamUrl = nextStreamUrl
  closeSocket()

  try {
    socket = new WebSocket(resolveSimulationStreamUrl(sessionId, currentStreamUrl))
  } catch {
    notifyConnection(false)
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    reconnectAttempts = 0
    lastStreamMessageAt = Date.now()
    startStreamWatchdog()
    notifyConnection(true)
  }

  socket.onmessage = (event) => {
    lastStreamMessageAt = Date.now()
    decodeMessage(String(event.data), sessionId, generation)
  }

  socket.onerror = () => {
    notifyConnection(false)
    socket?.close()
  }

  socket.onclose = () => {
    stopStreamWatchdog()
    notifyConnection(false)
    scheduleReconnect()
  }
}

export function registerSimulationStreamHandler(handler: MessageHandler): () => void {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

export function registerSimulationStreamConnectionListener(
  listener: (connected: boolean) => void,
): () => void {
  connectionListeners.add(listener)
  listener(Boolean(socket && socket.readyState === WebSocket.OPEN))
  return () => {
    connectionListeners.delete(listener)
  }
}

export function getCurrentStreamSessionId(): string {
  return currentSessionId
}
