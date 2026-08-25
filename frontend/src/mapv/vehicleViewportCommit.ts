import type { VehicleTwinSample } from './vehicleTwinSample.ts'

export interface VehicleTwinCommitTarget {
  reset(): void
  push(samples: VehicleTwinSample[]): void
}

export function replaceTwinFrameAtomically(
  twin: VehicleTwinCommitTarget,
  samples: readonly VehicleTwinSample[],
  initialSampleSpacingMs: number,
): number {
  if (samples.length === 0) return 0
  const committed = samples.map((sample) => ({
    ...sample,
    point: [...sample.point] as [number, number, number],
  }))
  const priming = committed.map((sample) => ({
    ...sample,
    point: [...sample.point] as [number, number, number],
    time: sample.time - initialSampleSpacingMs,
  }))
  twin.reset()
  twin.push(priming)
  twin.push(committed)
  return committed.length
}
