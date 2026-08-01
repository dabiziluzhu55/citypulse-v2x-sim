import type { SimulationWsMessage } from '../types/simulation'

type MessageHandler = (message: SimulationWsMessage) => void

let socket: WebSocket | null = null
let currentSessionId = ''
let currentStreamUrl = ''
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
const MAX_RECONNECT_DELAY_MS = 30_000
let shouldReconnect = false
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

function closeSocket() {
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

export function connectSimulationStream(sessionId: string, backendStreamUrl = '') {
  if (!sessionId) {
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
    notifyConnection(true)
  }

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as unknown
      if (!isSimulationMessage(payload)) {
        return
      }
      for (const handler of handlers) {
        handler(payload)
      }
    } catch {
      // ignore malformed payloads
    }
  }

  socket.onerror = () => {
    notifyConnection(false)
  }

  socket.onclose = () => {
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
