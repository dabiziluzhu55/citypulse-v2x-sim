type RunMessageHandler = (payload: Record<string, unknown>) => void

let socket: WebSocket | null = null
let currentRunId = ''
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
const MAX_RECONNECT_DELAY_MS = 30_000
let shouldReconnect = false
const handlers = new Set<RunMessageHandler>()
const connectionListeners = new Set<(connected: boolean) => void>()

function buildRunWsUrl(runId: string): string {
  const configuredUrl = import.meta.env.VITE_TRAFFIC_WS_URL
  if (configuredUrl) {
    return configuredUrl.replace('{run_id}', encodeURIComponent(runId))
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}/api/v1/ws/runs/${encodeURIComponent(runId)}`
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
  if (!shouldReconnect || !currentRunId) {
    return
  }

  clearReconnectTimer()
  reconnectAttempts += 1
  const delay = Math.min(3_000 * 2 ** (reconnectAttempts - 1), MAX_RECONNECT_DELAY_MS)
  reconnectTimer = setTimeout(() => {
    connectRunWebSocket(currentRunId)
  }, delay)
}

export function connectRunWebSocket(runId: string) {
  if (!runId) {
    shouldReconnect = false
    clearReconnectTimer()
    closeSocket()
    currentRunId = ''
    notifyConnection(false)
    return
  }

  if (runId === currentRunId && socket && socket.readyState === WebSocket.OPEN) {
    return
  }

  shouldReconnect = true
  currentRunId = runId
  closeSocket()

  try {
    socket = new WebSocket(buildRunWsUrl(runId))
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
      const payload = JSON.parse(String(event.data)) as Record<string, unknown>
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

export function registerRunWebSocketHandler(handler: RunMessageHandler): () => void {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

export function registerRunWebSocketConnectionListener(
  listener: (connected: boolean) => void,
): () => void {
  connectionListeners.add(listener)
  listener(Boolean(socket && socket.readyState === WebSocket.OPEN))
  return () => {
    connectionListeners.delete(listener)
  }
}

export function getCurrentRunWebSocketId(): string {
  return currentRunId
}
