export interface CameraFlightGuard {
  complete: () => void
  cancel: () => void
}

export interface CameraFlightGuardOptions {
  timeoutMs: number
  onComplete: () => void
  onTimeout: () => void
}

export const CAMERA_FLIGHT_WATCHDOG_MINIMUM_MS = 1_500
export const CAMERA_FLIGHT_WATCHDOG_GRACE_MS = 1_200

export function cameraFlightWatchdogDelay(durationMs: number): number {
  const duration = Number.isFinite(durationMs) ? Math.max(0, durationMs) : 0
  return Math.max(
    CAMERA_FLIGHT_WATCHDOG_MINIMUM_MS,
    duration + CAMERA_FLIGHT_WATCHDOG_GRACE_MS,
  )
}

export function createCameraFlightGuard(
  options: CameraFlightGuardOptions,
): CameraFlightGuard {
  let finished = false
  let timer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
    if (finished) return
    finished = true
    timer = null
    options.onTimeout()
    options.onComplete()
  }, Math.max(0, options.timeoutMs))

  return {
    complete: () => {
      if (finished) return
      finished = true
      if (timer) clearTimeout(timer)
      timer = null
      options.onComplete()
    },
    cancel: () => {
      if (finished) return
      finished = true
      if (timer) clearTimeout(timer)
      timer = null
    },
  }
}
