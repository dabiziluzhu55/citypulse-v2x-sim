import type { SimulationSnapshot } from '../types/simulation'

type OrderedSnapshot = Pick<
  SimulationSnapshot,
  'session_id' | 'sequence' | 'elapsed_seconds' | 'state'
>

export function shouldApplySimulationSnapshot(
  current: OrderedSnapshot | null,
  next: OrderedSnapshot,
  expectedSessionId = '',
): boolean {
  if (expectedSessionId && next.session_id !== expectedSessionId) return false
  if (!current || current.session_id !== next.session_id) return true
  if (next.sequence !== current.sequence) return next.sequence > current.sequence
  if (next.elapsed_seconds !== current.elapsed_seconds) {
    return next.elapsed_seconds > current.elapsed_seconds
  }
  return next.state !== current.state
}
