import type { TrafficIntersectionView } from '../types/traffic'

interface SignalFrame {
  sessionId: string
  elapsedSeconds: number
  intersections: TrafficIntersectionView[]
}

const MAX_SIGNAL_FRAMES = 64

export class SignalDisplayTimeline {
  private frames: SignalFrame[] = []

  push(
    sessionId: string,
    elapsedSeconds: number,
    intersections: TrafficIntersectionView[] | null | undefined,
  ): void {
    if (!sessionId || !Number.isFinite(elapsedSeconds)) return
    if (this.frames.at(-1)?.sessionId !== sessionId) this.frames = []
    const frame: SignalFrame = {
      sessionId,
      elapsedSeconds,
      intersections: (intersections ?? []).map((intersection) => ({ ...intersection })),
    }
    const existing = this.frames.findIndex((item) => item.elapsedSeconds === elapsedSeconds)
    if (existing >= 0) this.frames[existing] = frame
    else this.frames.push(frame)
    this.frames.sort((left, right) => left.elapsedSeconds - right.elapsedSeconds)
    if (this.frames.length > MAX_SIGNAL_FRAMES) {
      this.frames.splice(0, this.frames.length - MAX_SIGNAL_FRAMES)
    }
  }

  sample(sessionId: string, displayElapsedSeconds: number): TrafficIntersectionView[] | null {
    if (!sessionId || !Number.isFinite(displayElapsedSeconds)) return null
    const frame = [...this.frames].reverse().find((candidate) => (
      candidate.sessionId === sessionId
      && candidate.elapsedSeconds <= displayElapsedSeconds + 1e-9
    ))
    return frame?.intersections ?? null
  }

  clear(): void {
    this.frames = []
  }
}
