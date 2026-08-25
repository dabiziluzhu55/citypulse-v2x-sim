export const MAX_VEHICLE_OUTPUT_CATCH_UP_RATE = 1.05

export interface VehicleOutputPacingFrame {
  sampleWallTimeMs: number
  backlogMs: number
  catchingUp: boolean
}

export class VehicleOutputPacer {
  private lastOutputWallTimeMs: number | null = null
  private nextOutputDueMs: number | null = null
  private sampleWallTimeMs: number | null = null
  private backlogMs = 0

  next(wallTimeMs: number, frameIntervalMs: number): VehicleOutputPacingFrame | null {
    if (!Number.isFinite(wallTimeMs) || !Number.isFinite(frameIntervalMs) || frameIntervalMs <= 0) {
      return null
    }
    if (this.nextOutputDueMs != null && wallTimeMs < this.nextOutputDueMs - 1) return null
    const actualGapMs = this.lastOutputWallTimeMs == null
      ? frameIntervalMs
      : Math.max(0, wallTimeMs - this.lastOutputWallTimeMs)
    this.lastOutputWallTimeMs = wallTimeMs
    const previousDueMs = this.nextOutputDueMs ?? wallTimeMs
    this.nextOutputDueMs = previousDueMs + frameIntervalMs
    if (this.nextOutputDueMs < wallTimeMs - frameIntervalMs) {
      this.nextOutputDueMs = wallTimeMs + frameIntervalMs
    }

    if (this.sampleWallTimeMs == null) {
      this.sampleWallTimeMs = wallTimeMs
    } else {
      if (actualGapMs > frameIntervalMs * 1.5) {
        this.backlogMs += actualGapMs - frameIntervalMs
      } else if (actualGapMs < frameIntervalMs) {
        this.backlogMs = Math.max(0, this.backlogMs - (frameIntervalMs - actualGapMs))
      }
      const maximumCatchUpMs = frameIntervalMs * (MAX_VEHICLE_OUTPUT_CATCH_UP_RATE - 1)
      const consumedCatchUpMs = Math.min(this.backlogMs, maximumCatchUpMs)
      this.sampleWallTimeMs += frameIntervalMs + consumedCatchUpMs
      this.backlogMs -= consumedCatchUpMs
    }
    return {
      sampleWallTimeMs: this.sampleWallTimeMs,
      backlogMs: this.backlogMs,
      catchingUp: this.backlogMs > 0,
    }
  }

  reset(): void {
    this.lastOutputWallTimeMs = null
    this.nextOutputDueMs = null
    this.sampleWallTimeMs = null
    this.backlogMs = 0
  }
}
