import type { SimulationIntersectionRuntime, SimulationState } from '../types/simulation'
import type { TrafficStateView, TrafficVehicleView } from '../types/traffic'
import {
  VehiclePresentationTimeline,
  type VehiclePresentationSample,
  type VehiclePresentationTimelineStats,
} from './vehiclePresentationTimeline.ts'

export interface AuthoritativeVehicleFrame {
  sessionId: string
  sequence: number
  elapsedSeconds: number
  arrivalTimeMs: number
  state: SimulationState
  playbackRate: number
  view: TrafficStateView
  intersectionRuntimeById: Readonly<Record<string, SimulationIntersectionRuntime>>
}

export interface AuthoritativeVehicleHistoryWindow {
  sessionId: string
  displayElapsedSeconds: number
  frames: readonly AuthoritativeVehicleFrame[]
  leftFrame: AuthoritativeVehicleFrame | null
  rightFrame: AuthoritativeVehicleFrame | null
}

export type PresentedVehiclePose = TrafficVehicleView

export interface PresentedVehicleFrame extends VehiclePresentationSample {
  sessionId: string
  vehicles: PresentedVehiclePose[]
}

export interface VehicleGeometryGeneration {
  sumoNetworkSha256: string
  visualManifestSha256: string
}

export class VehiclePresentationCoordinator {
  private readonly timeline = new VehiclePresentationTimeline()
  private authoritativeFrames: AuthoritativeVehicleFrame[] = []

  push(
    view: TrafficStateView,
    sequence: number,
    state: SimulationState,
    playbackRate: number | null | undefined,
    arrivalTimeMs: number,
    intersectionRuntimeById: Readonly<Record<string, SimulationIntersectionRuntime>> = {},
  ): boolean {
    const accepted = this.timeline.push(view, sequence, state, playbackRate, arrivalTimeMs)
    if (!accepted) return false
    const frame: AuthoritativeVehicleFrame = {
      sessionId: view.session_id,
      sequence,
      elapsedSeconds: view.elapsed_seconds,
      arrivalTimeMs,
      state,
      playbackRate: Math.max(0, Number(playbackRate) || 0),
      view,
      intersectionRuntimeById: Object.fromEntries(
        Object.entries(intersectionRuntimeById).map(([intersectionId, runtime]) => [
          intersectionId,
          {
            ...runtime,
            lanes: Object.fromEntries(
              Object.entries(runtime.lanes).map(([laneId, lane]) => [laneId, { ...lane }]),
            ),
          },
        ]),
      ),
    }
    if (
      this.authoritativeFrames.length > 0
      && this.authoritativeFrames.at(-1)?.sessionId !== frame.sessionId
    ) this.authoritativeFrames = []
    this.authoritativeFrames.push(frame)
    this.pruneAuthoritativeHistory()
    return true
  }

  tick(wallTimeMs: number): number | null {
    return this.timeline.tick(wallTimeMs)
  }

  sample(): PresentedVehicleFrame | null {
    const sample = this.timeline.sample()
    if (!sample) return null
    return {
      ...sample,
      sessionId: sample.view.session_id,
      vehicles: sample.view.vehicles,
    }
  }

  authoritativeHistoryWindow(
    displayElapsedSeconds: number | null = this.sample()?.elapsedSeconds ?? null,
    historySeconds = 6,
  ): AuthoritativeVehicleHistoryWindow | null {
    if (!Number.isFinite(displayElapsedSeconds) || this.authoritativeFrames.length === 0) return null
    const elapsedSeconds = Number(displayElapsedSeconds)
    let rightIndex = this.authoritativeFrames.findIndex((frame) => (
      frame.elapsedSeconds > elapsedSeconds
    ))
    if (rightIndex < 0) rightIndex = this.authoritativeFrames.length
    const leftFrame = rightIndex > 0 ? this.authoritativeFrames[rightIndex - 1] : null
    const rightFrame = rightIndex < this.authoritativeFrames.length
      ? this.authoritativeFrames[rightIndex]
      : null
    const cutoff = elapsedSeconds - Math.max(6, historySeconds)
    const firstIndex = Math.max(
      0,
      this.authoritativeFrames.findIndex((frame) => frame.elapsedSeconds >= cutoff),
    )
    const frames = this.authoritativeFrames.slice(firstIndex)
    if (firstIndex > 0) frames.unshift(this.authoritativeFrames[firstIndex - 1])
    return {
      sessionId: this.authoritativeFrames.at(-1)?.sessionId ?? '',
      displayElapsedSeconds: elapsedSeconds,
      frames,
      leftFrame,
      rightFrame,
    }
  }

  reset(sessionId = ''): void {
    this.timeline.reset(sessionId)
    this.authoritativeFrames = []
  }

  stats(): VehiclePresentationTimelineStats {
    return this.timeline.stats()
  }

  private pruneAuthoritativeHistory(): void {
    const latest = this.authoritativeFrames.at(-1)
    if (!latest) return
    const displayElapsedSeconds = this.timeline.stats().displayElapsedSeconds
    const cutoff = Math.min(
      latest.elapsedSeconds - 30,
      displayElapsedSeconds == null
        ? latest.elapsedSeconds - 30
        : displayElapsedSeconds - 1,
    )
    while (
      this.authoritativeFrames.length > 2
      && this.authoritativeFrames[1].elapsedSeconds < cutoff
    ) this.authoritativeFrames.shift()
  }
}
