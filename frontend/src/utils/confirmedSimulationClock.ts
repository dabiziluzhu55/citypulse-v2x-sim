const MIN_TRANSITION_MS = 120
const MAX_TRANSITION_MS = 3_000
const SECONDS_PER_DAY = 24 * 60 * 60

export interface ConfirmedSimulationTimeSample {
  sequence: number
  officialTime: string
  state: string | null
  arrivalTimeMs: number
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function parseOfficialTimeSeconds(value: string): number | null {
  const time = value.includes('T') ? value.split('T')[1] ?? '' : value
  const match = /^(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?/.exec(time)
  if (!match) return null
  const hours = Number(match[1])
  const minutes = Number(match[2])
  const seconds = Number(match[3])
  if (hours > 23 || minutes > 59 || seconds > 59) return null
  return hours * 3_600 + minutes * 60 + seconds
}

export function formatOfficialTimeSeconds(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--:--:--'
  const normalized = ((Math.floor(value) % SECONDS_PER_DAY) + SECONDS_PER_DAY) % SECONDS_PER_DAY
  const hours = Math.floor(normalized / 3_600)
  const minutes = Math.floor((normalized % 3_600) / 60)
  const seconds = normalized % 60
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
}

function unwrapDay(previous: number, next: number): number {
  if (next + SECONDS_PER_DAY / 2 < previous) return next + SECONDS_PER_DAY
  if (next - SECONDS_PER_DAY / 2 > previous) return next - SECONDS_PER_DAY
  return next
}

function shouldAnimate(state: string | null): boolean {
  return state === 'STARTING' || state === 'RUNNING' || state === 'STOPPING'
}

export class ConfirmedSimulationClock {
  private sequence = Number.NEGATIVE_INFINITY
  private fromSeconds: number | null = null
  private targetSeconds: number | null = null
  private transitionStartedAtMs = 0
  private transitionDurationMs = 0
  private lastArrivalTimeMs: number | null = null
  private advancing = false

  accept(sample: ConfirmedSimulationTimeSample): boolean {
    const parsed = parseOfficialTimeSeconds(sample.officialTime)
    if (parsed == null || !Number.isFinite(sample.arrivalTimeMs) || sample.sequence <= this.sequence) {
      return false
    }
    const next = this.targetSeconds == null ? parsed : unwrapDay(this.targetSeconds, parsed)
    const current = this.valueAt(sample.arrivalTimeMs) ?? next
    const interval = this.lastArrivalTimeMs == null
      ? 0
      : sample.arrivalTimeMs - this.lastArrivalTimeMs
    const animate = shouldAnimate(sample.state) && this.targetSeconds != null && next >= current

    this.sequence = sample.sequence
    this.fromSeconds = animate ? Math.min(current, next) : next
    this.targetSeconds = next
    this.transitionStartedAtMs = sample.arrivalTimeMs
    this.transitionDurationMs = animate && interval > 0 && next > current
      ? clamp(interval, MIN_TRANSITION_MS, MAX_TRANSITION_MS)
      : 0
    this.lastArrivalTimeMs = sample.arrivalTimeMs
    this.advancing = animate
    return true
  }

  valueAt(wallTimeMs: number): number | null {
    if (this.fromSeconds == null || this.targetSeconds == null) return null
    if (!this.advancing || this.transitionDurationMs <= 0) return this.targetSeconds
    const ratio = clamp(
      (wallTimeMs - this.transitionStartedAtMs) / this.transitionDurationMs,
      0,
      1,
    )
    return Math.min(
      this.targetSeconds,
      this.fromSeconds + (this.targetSeconds - this.fromSeconds) * ratio,
    )
  }

  reset(): void {
    this.sequence = Number.NEGATIVE_INFINITY
    this.fromSeconds = null
    this.targetSeconds = null
    this.transitionStartedAtMs = 0
    this.transitionDurationMs = 0
    this.lastArrivalTimeMs = null
    this.advancing = false
  }
}
