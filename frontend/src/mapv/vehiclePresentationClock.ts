const MIN_PRESENTATION_STEP_MS = 1

export class VehiclePresentationClock {
  private lastTime = Number.NEGATIVE_INFINITY

  next(now = Date.now()): number {
    const safeNow = Number.isFinite(now) ? now : Date.now()
    this.lastTime = Number.isFinite(this.lastTime)
      ? Math.max(safeNow, this.lastTime + MIN_PRESENTATION_STEP_MS)
      : safeNow
    return this.lastTime
  }

  reset(): void {
    this.lastTime = Number.NEGATIVE_INFINITY
  }
}

export function isVehicleAnimationActive(state: string | null): boolean {
  return state === 'STARTING' || state === 'RUNNING' || state === 'STOPPING'
}
