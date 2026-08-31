export interface Map3dPerformanceStats {
  stableMode: boolean
  fps: number | null
  longTaskCount: number
}

export const MAP3D_NORMAL_FRAME_RATE = 30
export const MAP3D_STABLE_FRAME_RATE = 24
export const MAP3D_LOW_FPS_THRESHOLD = 26
const LOW_FPS_WINDOW_MS = 5_000
const LONG_TASK_THRESHOLD_MS = 100
const LONG_TASK_WINDOW_MS = 60_000
const LONG_TASK_LIMIT = 3

export class Map3dPerformanceGovernor {
  private frameTimes: number[] = []
  private longTaskTimes: number[] = []
  private lowFpsStartedAt: number | null = null
  private stableMode = false
  private fps: number | null = null

  constructor(initialStableMode = false) {
    this.stableMode = initialStableMode
  }

  recordFrame(nowMs: number): boolean {
    if (!Number.isFinite(nowMs)) return false
    this.frameTimes.push(nowMs)
    this.frameTimes = this.frameTimes.filter((time) => nowMs - time <= 1_000)
    if (this.frameTimes.length >= 2) {
      const duration = this.frameTimes.at(-1)! - this.frameTimes[0]
      this.fps = duration > 0 ? (this.frameTimes.length - 1) * 1_000 / duration : null
    }
    if (this.stableMode) return false
    if (this.fps != null && this.fps < MAP3D_LOW_FPS_THRESHOLD) {
      this.lowFpsStartedAt ??= nowMs
      if (nowMs - this.lowFpsStartedAt >= LOW_FPS_WINDOW_MS) return this.latchStableMode()
    } else {
      this.lowFpsStartedAt = null
    }
    return false
  }

  recordLongTask(durationMs: number, nowMs: number): boolean {
    if (!Number.isFinite(durationMs) || durationMs <= LONG_TASK_THRESHOLD_MS) return false
    this.longTaskTimes.push(nowMs)
    this.longTaskTimes = this.longTaskTimes.filter((time) => nowMs - time <= LONG_TASK_WINDOW_MS)
    if (!this.stableMode && this.longTaskTimes.length >= LONG_TASK_LIMIT) {
      return this.latchStableMode()
    }
    return false
  }

  stats(): Map3dPerformanceStats {
    return {
      stableMode: this.stableMode,
      fps: this.fps == null ? null : Math.round(this.fps),
      longTaskCount: this.longTaskTimes.length,
    }
  }

  forceStableMode(): boolean {
    return this.latchStableMode()
  }

  private latchStableMode(): boolean {
    if (this.stableMode) return false
    this.stableMode = true
    return true
  }
}
