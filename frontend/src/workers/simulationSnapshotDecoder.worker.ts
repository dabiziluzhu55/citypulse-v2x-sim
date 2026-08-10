import type { SimulationWsMessage } from '../types/simulation'

interface DecodeRequest {
  raw: string
  generation: number
  expectedSessionId: string
}

interface DecodeResponse {
  generation: number
  messages: SimulationWsMessage[]
  parseDurationMs: number
  coalescedSnapshotCount: number
}

const workerScope = self as unknown as {
  onmessage: ((event: MessageEvent<DecodeRequest>) => void) | null
  postMessage: (message: DecodeResponse) => void
}

let pending: Array<{ message: SimulationWsMessage; generation: number; parseDurationMs: number }> = []
let flushTimer: ReturnType<typeof setTimeout> | null = null

function isMessage(value: unknown, expectedSessionId: string): value is SimulationWsMessage {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<SimulationWsMessage> & { data?: { session_id?: unknown; sequence?: unknown } }
  if (candidate.type === 'heartbeat') return candidate.session_id === expectedSessionId
  return candidate.type === 'snapshot'
    && candidate.data?.session_id === expectedSessionId
    && Number.isFinite(candidate.data?.sequence)
}

function flush(): void {
  flushTimer = null
  if (pending.length === 0) return
  const generation = pending.at(-1)?.generation ?? 0
  const matching = pending.filter((entry) => entry.generation === generation)
  pending = []
  const snapshots = matching
    .filter((entry) => entry.message.type === 'snapshot')
    .sort((left, right) => (
      (left.message.type === 'snapshot' ? left.message.data.sequence : -1)
      - (right.message.type === 'snapshot' ? right.message.data.sequence : -1)
    ))
    .slice(-2)
  const heartbeat = [...matching].reverse().find((entry) => entry.message.type === 'heartbeat')
  const selected = [...snapshots, ...(heartbeat ? [heartbeat] : [])]
  workerScope.postMessage({
    generation,
    messages: selected.map((entry) => entry.message),
    parseDurationMs: matching.reduce((sum, entry) => sum + entry.parseDurationMs, 0),
    coalescedSnapshotCount: Math.max(0, matching.length - selected.length),
  })
}

workerScope.onmessage = (event) => {
  const startedAt = performance.now()
  try {
    const parsed = JSON.parse(event.data.raw) as unknown
    if (!isMessage(parsed, event.data.expectedSessionId)) return
    pending.push({
      message: parsed,
      generation: event.data.generation,
      parseDurationMs: performance.now() - startedAt,
    })
    if (pending.length > 8) pending = pending.slice(-8)
    if (flushTimer === null) flushTimer = setTimeout(flush, 16)
  } catch {
    // Malformed stream messages do not interrupt the active session.
  }
}

export {}
