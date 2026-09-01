import * as mapvthree from '@baidumap/mapv-three'
import type { VehicleTwinSample } from './vehicleTwinSample.ts'
import {
  VEHICLE_TWIN_PRIME_SPACING_MS,
  VEHICLE_TWIN_RENDER_DELAY_MS,
} from './vehicleTwinPresentation.ts'

export {
  VEHICLE_TWIN_PRIME_SPACING_MS,
  VEHICLE_TWIN_RENDER_DELAY_MS,
} from './vehicleTwinPresentation.ts'

interface TwinTickBuffers {
  id?: unknown[]
  payload?: Array<Record<string, unknown>>
}

interface TwinChannel {
  twin: mapvthree.Twin
  tickingListener: (event: Record<string, unknown>) => void
  primed: boolean
  frozen: boolean
  freezeWhenVisible: boolean
  submittedCount: number
  firstSubmittedTime: number | null
  latestSubmittedTime: number | null
  actualVisibleCount: number
  visibleVehicleIds: string[]
  visibleDisplayElapsedSeconds: number | null
  firstVisibleAtMs: number | null
  hasRenderedVehicles: boolean
  lastSubmittedVehicleIds: Set<string>
  warmupSamples: VehicleTwinSample[]
  readyListeners: Set<() => void>
}

export interface VehicleTwinVisibleState {
  visibleVehicleIds: string[]
  visibleDisplayElapsedSeconds: number | null
  actualVisibleCount: number
  submittedCount: number
  submittedWindowDepthMs: number
  windowExhaustionCount: number
  firstVisibleAtMs: number | null
  frozen: boolean
  replacementWarming: boolean
  replacementReady: boolean
}

export class VehicleTwinPresenter {
  private readonly engine: mapvthree.Engine
  private readonly modelConfig: Record<number, string | undefined>
  private activeChannel: TwinChannel
  private warmingChannel: TwinChannel | null = null
  private warmupFrameId: number | null = null
  private windowExhaustionCount = 0

  constructor(engine: mapvthree.Engine, modelConfig: Record<number, string | undefined>) {
    this.engine = engine
    this.modelConfig = modelConfig
    this.activeChannel = this.createChannel(true)
  }

  push(samples: VehicleTwinSample[]): number {
    return this.pushToChannel(this.activeChannel, samples)
  }

  beginReplacement(samples: VehicleTwinSample[]): boolean {
    if (samples.length === 0) return false
    this.cancelReplacement()
    // MapV skips beforeRender/ticking for an invisible Twin. Keep the warming
    // channel renderable until its first real buffer, then hide and freeze it
    // in handleTicking before the model buffer is painted.
    const channel = this.createChannel(true)
    channel.freezeWhenVisible = true
    channel.warmupSamples = samples.map((sample) => ({
      ...sample,
      point: [...sample.point] as [number, number, number],
    }))
    this.warmingChannel = channel
    this.pushToChannel(channel, samples)
    this.scheduleWarmupRender()
    return true
  }

  replacementIsReady(): boolean {
    return Boolean(this.warmingChannel?.actualVisibleCount)
  }

  async waitForReplacementReady(
    signal: AbortSignal,
    timeoutMs = VEHICLE_TWIN_RENDER_DELAY_MS + 1_500,
  ): Promise<boolean> {
    const channel = this.warmingChannel
    if (!channel) return true
    if (channel.actualVisibleCount > 0) return true
    return new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (ready: boolean) => {
        if (settled) return
        settled = true
        clearTimeout(timeoutId)
        signal.removeEventListener('abort', onAbort)
        channel.readyListeners.delete(onReady)
        resolve(ready)
      }
      const onReady = () => finish(true)
      const onAbort = () => finish(false)
      const timeoutId = setTimeout(() => finish(false), Math.max(0, timeoutMs))
      channel.readyListeners.add(onReady)
      signal.addEventListener('abort', onAbort, { once: true })
      this.scheduleWarmupRender()
    })
  }

  activateReplacement(): boolean {
    const replacement = this.warmingChannel
    if (!replacement || replacement.actualVisibleCount === 0) return false
    this.cancelWarmupRender()
    const previous = this.activeChannel
    this.warmingChannel = null
    ;(replacement.twin as unknown as { visible: boolean }).visible = true
    replacement.freezeWhenVisible = false
    if (replacement.frozen) {
      replacement.twin.start()
      replacement.frozen = false
    }
    this.activeChannel = replacement
    this.disposeChannel(previous)
    this.engine.requestRender()
    return true
  }

  cancelReplacement(): void {
    this.cancelWarmupRender()
    if (!this.warmingChannel) return
    this.disposeChannel(this.warmingChannel)
    this.warmingChannel = null
  }

  freezeAfterVisible(): void {
    const channel = this.activeChannel
    channel.freezeWhenVisible = true
    if (channel.actualVisibleCount > 0) this.freeze(channel)
  }

  resume(): void {
    const channel = this.activeChannel
    channel.freezeWhenVisible = false
    if (!channel.frozen) return
    channel.twin.start()
    channel.frozen = false
    this.engine.requestRender()
  }

  reset(_reason: string): void {
    this.cancelReplacement()
    this.windowExhaustionCount = 0
    const channel = this.activeChannel
    if (channel.frozen) channel.twin.start()
    channel.twin.reset()
    channel.primed = false
    channel.frozen = false
    channel.freezeWhenVisible = false
    channel.submittedCount = 0
    channel.firstSubmittedTime = null
    channel.latestSubmittedTime = null
    channel.actualVisibleCount = 0
    channel.visibleVehicleIds = []
    channel.visibleDisplayElapsedSeconds = null
    channel.firstVisibleAtMs = null
    channel.hasRenderedVehicles = false
    channel.lastSubmittedVehicleIds.clear()
  }

  state(): VehicleTwinVisibleState {
    const channel = this.activeChannel
    return {
      visibleVehicleIds: [...channel.visibleVehicleIds],
      visibleDisplayElapsedSeconds: channel.visibleDisplayElapsedSeconds,
      actualVisibleCount: channel.actualVisibleCount,
      submittedCount: channel.submittedCount,
      submittedWindowDepthMs:
        channel.firstSubmittedTime == null || channel.latestSubmittedTime == null
          ? 0
          : Math.max(0, channel.latestSubmittedTime - channel.firstSubmittedTime),
      windowExhaustionCount: this.windowExhaustionCount,
      firstVisibleAtMs: channel.firstVisibleAtMs,
      frozen: channel.frozen,
      replacementWarming: this.warmingChannel !== null,
      replacementReady: this.replacementIsReady(),
    }
  }

  destroy(): void {
    this.cancelReplacement()
    this.disposeChannel(this.activeChannel)
  }

  private createChannel(visible: boolean): TwinChannel {
    const twin = this.engine.add(new mapvthree.Twin({
      delay: VEHICLE_TWIN_RENDER_DELAY_MS,
      modelConfig: this.modelConfig,
      keepSize: false,
    }))
    const channel: TwinChannel = {
      twin,
      tickingListener: (_event: Record<string, unknown>): void => {},
      primed: false,
      frozen: false,
      freezeWhenVisible: false,
      submittedCount: 0,
      firstSubmittedTime: null,
      latestSubmittedTime: null,
      actualVisibleCount: 0,
      visibleVehicleIds: [],
      visibleDisplayElapsedSeconds: null,
      firstVisibleAtMs: null,
      hasRenderedVehicles: false,
      lastSubmittedVehicleIds: new Set<string>(),
      warmupSamples: [],
      readyListeners: new Set<() => void>(),
    }
    channel.tickingListener = (event) => this.handleTicking(channel, event)
    twin.addEventListener('ticking', channel.tickingListener)
    ;(twin as unknown as { visible: boolean }).visible = visible
    return channel
  }

  private pushToChannel(channel: TwinChannel, samples: VehicleTwinSample[]): number {
    if (samples.length === 0) return 0
    if (channel.frozen) {
      channel.twin.start()
      channel.frozen = false
    }
    const time = Number(samples[0]?.time)
    const reenteredSamples = channel.primed
      ? samples.filter((sample) => !channel.lastSubmittedVehicleIds.has(sample.id))
      : []
    if (reenteredSamples.length > 0 && channel.latestSubmittedTime != null) {
      // MapV does not clamp a newly created entity's interpolation ratio. Give
      // every re-entering vehicle a zero-distance segment while preserving the
      // Twin channel's globally increasing timestamps.
      const primeTime = Math.max(
        channel.latestSubmittedTime + 1,
        time - VEHICLE_TWIN_PRIME_SPACING_MS,
      )
      if (primeTime < time) {
        channel.twin.push(reenteredSamples.map((sample) => ({
          ...sample,
          point: [...sample.point] as [number, number, number],
          time: primeTime,
          sampleQuality: 'held' as const,
          poseSource: 'held' as const,
          sourceSpeedMetersPerSecond: 0,
        })))
      }
    }
    if (!channel.primed) {
      const priming = samples.map((sample) => ({
        ...sample,
        point: [...sample.point] as [number, number, number],
        time: Number(sample.time) - VEHICLE_TWIN_PRIME_SPACING_MS,
      }))
      channel.twin.push(priming)
      channel.firstSubmittedTime = priming[0]?.time ?? time
      channel.primed = true
    }
    channel.twin.push(samples)
    channel.latestSubmittedTime = time
    channel.submittedCount = samples.length
    channel.lastSubmittedVehicleIds = new Set(samples.map((sample) => sample.id))
    this.engine.requestRender()
    return samples.length
  }

  private handleTicking(channel: TwinChannel, event: Record<string, unknown>): void {
    const buffers = event.buffers as TwinTickBuffers | undefined
    const ids = Array.isArray(buffers?.id)
      ? buffers.id.filter((id): id is string => typeof id === 'string')
      : []
    const previousCount = channel.actualVisibleCount
    channel.actualVisibleCount = ids.length
    channel.visibleVehicleIds = ids
    const payload = Array.isArray(buffers?.payload) ? buffers.payload : []
    const elapsedSeconds = Number(payload.find((item) => (
      Number.isFinite(Number(item.displayElapsedSeconds))
    ))?.displayElapsedSeconds)
    channel.visibleDisplayElapsedSeconds = Number.isFinite(elapsedSeconds)
      ? elapsedSeconds
      : null
    if (ids.length > 0) {
      channel.hasRenderedVehicles = true
      channel.firstVisibleAtMs ??= performance.now()
      if (channel === this.warmingChannel) {
        ;(channel.twin as unknown as { visible: boolean }).visible = false
      }
      for (const listener of channel.readyListeners) listener()
      channel.readyListeners.clear()
    } else if (
      channel === this.activeChannel
      && previousCount > 0
      && channel.hasRenderedVehicles
      && !channel.frozen
    ) {
      this.windowExhaustionCount += 1
    }
    if (channel.freezeWhenVisible && ids.length > 0) this.freeze(channel)
  }

  private freeze(channel: TwinChannel): void {
    if (channel.frozen) return
    channel.twin.pause()
    channel.frozen = true
    channel.freezeWhenVisible = false
  }

  private scheduleWarmupRender(): void {
    if (this.warmupFrameId !== null || !this.warmingChannel) return
    const tick = (wallTimeMs: number) => {
      this.warmupFrameId = null
      const channel = this.warmingChannel
      if (!channel || channel.actualVisibleCount > 0) return
      const latestTime = channel.latestSubmittedTime ?? wallTimeMs
      if (wallTimeMs - latestTime >= VEHICLE_TWIN_PRIME_SPACING_MS) {
        const time = Math.max(wallTimeMs, latestTime + 1)
        const samples = channel.warmupSamples.map((sample) => ({
          ...sample,
          point: [...sample.point] as [number, number, number],
          time,
        }))
        channel.warmupSamples = samples
        this.pushToChannel(channel, samples)
      }
      this.engine.requestRender()
      this.warmupFrameId = requestAnimationFrame(tick)
    }
    this.warmupFrameId = requestAnimationFrame(tick)
  }

  private cancelWarmupRender(): void {
    if (this.warmupFrameId !== null) cancelAnimationFrame(this.warmupFrameId)
    this.warmupFrameId = null
  }

  private disposeChannel(channel: TwinChannel): void {
    channel.readyListeners.clear()
    channel.twin.removeEventListener('ticking', channel.tickingListener)
    this.engine.remove(channel.twin)
    channel.twin.dispose()
  }
}
