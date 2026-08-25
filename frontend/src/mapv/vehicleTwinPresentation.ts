export const VEHICLE_TWIN_RENDER_DELAY_MS = 500
export const VEHICLE_TWIN_PRIME_SPACING_MS = 50

function twinOffsetCorrectionThreshold(delayMs: number): number {
  return Math.min(Math.max(delayMs / 5, 200), 1_000)
}

/**
 * Small executable model of MapV Twin's global time window. It intentionally
 * mirrors EntityManager.push/shift so tests catch a one-sample exhausted
 * window without importing MapV or WebGL.
 */
export class MapvTwinWindowProbe {
  private readonly delayMs: number
  private sampleTimes: number[] = []
  private startWallTimeMs: number | null = null
  private timeOffsetMs = 0

  constructor(delayMs = VEHICLE_TWIN_RENDER_DELAY_MS) {
    this.delayMs = Math.max(0, delayMs)
  }

  push(sampleTimeMs: number, wallTimeMs: number): void {
    if (!Number.isFinite(sampleTimeMs) || !Number.isFinite(wallTimeMs)) return
    if (this.startWallTimeMs == null) {
      this.startWallTimeMs = wallTimeMs
      this.timeOffsetMs = wallTimeMs - sampleTimeMs
    } else {
      const nextOffset = wallTimeMs - sampleTimeMs
      if (
        nextOffset - this.timeOffsetMs
        > twinOffsetCorrectionThreshold(this.delayMs)
      ) this.timeOffsetMs = nextOffset
    }
    this.sampleTimes.push(sampleTimeMs)
  }

  tick(wallTimeMs: number): boolean {
    if (
      this.startWallTimeMs == null
      || wallTimeMs - this.delayMs < this.startWallTimeMs
    ) return false
    const queryTimeMs = wallTimeMs - this.delayMs - this.timeOffsetMs
    let shifted = 0
    for (let index = 1; index < this.sampleTimes.length; index += 1) {
      if (queryTimeMs < this.sampleTimes[index]) break
      shifted += 1
    }
    if (shifted > 0) this.sampleTimes.splice(0, shifted)
    return this.sampleTimes.length >= 2
  }

  depthMs(): number {
    if (this.sampleTimes.length < 2) return 0
    return Math.max(0, this.sampleTimes.at(-1)! - this.sampleTimes[0])
  }
}
