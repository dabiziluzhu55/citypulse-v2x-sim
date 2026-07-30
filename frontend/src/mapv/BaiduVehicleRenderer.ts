import * as mapvthree from '@baidumap/mapv-three'
import type { SimulationState } from '../types/simulation'
import type { TrafficVehicleView } from '../types/traffic'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'
import type { RoadCoordinateProjector } from './roadGeometry'
import {
  resolveVehicleRenderRadius,
  StableVehicleSelector,
} from './vehicleVisibility'
import {
  createVehicleTwinSample,
  type VehicleTwinSample,
} from './vehicleTwinSample'
import { resolveVehicleModelProfile } from './vehicleModelProfiles.ts'
import {
  resolveStableVehicleHeading,
  type VehicleHeadingState,
} from './vehicleOrientation.ts'
import type {
  LaneHeadingResolver,
  LanePoseResolver,
} from './realistic/intersectionLaneHeading.ts'
import {
  isVehicleAnimationActive,
  VehiclePresentationClock,
} from './vehiclePresentationClock'
import { VehicleMotionBuffer } from './vehicleMotionBuffer'

const TWIN_INTERPOLATION_DELAY_MS = 32
const VEHICLE_HISTORY_TTL_SNAPSHOTS = 30

export interface VehicleRenderContext {
  sessionId: string
  state: SimulationState | null
  sequence: number
  elapsedSeconds: number
}

export interface VehicleRenderStats {
  inputCount: number
  visibleCount: number
  radiusMeters: number
}

export class BaiduVehicleRenderer {
  private readonly engine: mapvthree.Engine
  private readonly twin: mapvthree.Twin
  private readonly projector: RoadCoordinateProjector
  private readonly selector = new StableVehicleSelector()
  private readonly poseHistory = new Map<string, VehicleHeadingState>()
  private readonly laneTrackHistory = new Map<string, { laneId: string; trackKey: string }>()
  private readonly historyLastSeenSequence = new Map<string, number>()
  private readonly presentationClock = new VehiclePresentationClock()
  private readonly motionBuffer = new VehicleMotionBuffer()
  private outputFrameId: number | null = null
  private laneHeadingResolver: LaneHeadingResolver | null = null
  private lanePoseResolver: LanePoseResolver | null = null
  private lastVehicles: TrafficVehicleView[] = []
  private lastContext: VehicleRenderContext = {
    sessionId: '',
    state: null,
    sequence: -1,
    elapsedSeconds: 0,
  }
  private sessionId = ''
  private visibleCount = 0
  private primed = false

  constructor(
    engine: mapvthree.Engine,
    projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
  ) {
    this.engine = engine
    this.projector = projector
    this.twin = engine.add(new mapvthree.Twin({
      delay: TWIN_INTERPOLATION_DELAY_MS,
      modelConfig: {
        3: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.CAR,
        6: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.BUS,
        10: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.TRUCK,
      },
      keepSize: false,
    }))
    this.scheduleMotionFrame()
  }

  setLaneHeadingResolver(resolver: LaneHeadingResolver | null): void {
    this.laneHeadingResolver = resolver
  }

  setLanePoseResolver(resolver: LanePoseResolver | null): void {
    this.lanePoseResolver = resolver
    this.poseHistory.clear()
    this.laneTrackHistory.clear()
    this.historyLastSeenSequence.clear()
  }

  update(
    vehicles: TrafficVehicleView[],
    context: VehicleRenderContext,
    force = false,
  ): VehicleRenderStats {
    const previousSnapshotKey = `${this.lastContext.sessionId}:${this.lastContext.sequence}`
    const previousState = this.lastContext.state
    if (this.sessionId && context.sessionId && this.sessionId !== context.sessionId) {
      this.resetRuntime()
    }
    this.sessionId = context.sessionId
    const cameraRange = this.engine.map.getRange()
    const radiusMeters = resolveVehicleRenderRadius(cameraRange)
    const snapshotKey = `${context.sessionId}:${context.sequence}`
    const sameSession = context.sessionId === this.lastContext.sessionId
    const staleSnapshot = sameSession && (
      context.sequence < this.lastContext.sequence
      || (
        context.sequence === this.lastContext.sequence
        && context.elapsedSeconds <= this.lastContext.elapsedSeconds
      )
    )
    if (!force && (snapshotKey === previousSnapshotKey || staleSnapshot)) {
      return { inputCount: vehicles.length, visibleCount: this.visibleCount, radiusMeters }
    }
    this.lastVehicles = vehicles
    this.lastContext = context
    if (context.state === 'PAUSED' || previousState === 'PAUSED') this.motionBuffer.pause()
    if (
      vehicles.length === 0
      && (context.state === 'STOPPED' || context.state === 'COMPLETED' || context.state === 'FAILED')
    ) {
      this.resetRuntime()
      return { inputCount: 0, visibleCount: 0, radiusMeters }
    }
    const visible = this.selector.select(
      vehicles,
      this.projector,
      this.engine.map.getCenter(),
      cameraRange,
      snapshotKey,
    )
    this.visibleCount = visible.length
    if (visible.length === 0) {
      this.motionBuffer.push({
        sequence: context.sequence,
        elapsedSeconds: context.elapsedSeconds,
        arrivalTimeMs: performance.now(),
        samples: [],
      })
      this.pruneHistory(context.sequence)
      return { inputCount: vehicles.length, visibleCount: 0, radiusMeters }
    }
    const sourceTime = context.elapsedSeconds * 1_000
    const activeIds = new Set<string>()
    const samples = visible.map(({ vehicle, longitude, latitude }) => {
      const previous = this.poseHistory.get(vehicle.vehicle_id)
      const previousLaneTrack = this.laneTrackHistory.get(vehicle.vehicle_id)
      const profile = resolveVehicleModelProfile(vehicle.type_id)
      const lanePose = this.lanePoseResolver?.(
        vehicle.lane_id,
        [longitude, latitude],
        profile.targetLengthMeters / 2,
        previousLaneTrack?.laneId,
        previousLaneTrack?.trackKey,
      )
      const renderLongitude = lanePose?.longitude ?? longitude
      const renderLatitude = lanePose?.latitude ?? latitude
      const point = { longitude: renderLongitude, latitude: renderLatitude }
      const resolved = resolveStableVehicleHeading({
        sumoAngleDegrees: vehicle.angle,
        speedMetersPerSecond: vehicle.speed,
        current: point,
        timeSeconds: context.elapsedSeconds,
        laneHeading: lanePose?.heading
          ?? this.laneHeadingResolver?.(vehicle.lane_id, vehicle.lane_position),
      }, previous ?? null)
      const heading = resolved.heading
      this.poseHistory.set(vehicle.vehicle_id, resolved.state)
      this.historyLastSeenSequence.set(vehicle.vehicle_id, context.sequence)
      if (lanePose) {
        this.laneTrackHistory.set(vehicle.vehicle_id, {
          laneId: vehicle.lane_id,
          trackKey: lanePose.trackKey,
        })
      } else {
        this.laneTrackHistory.delete(vehicle.vehicle_id)
      }
      activeIds.add(vehicle.vehicle_id)
      return createVehicleTwinSample(
        vehicle,
        renderLongitude,
        renderLatitude,
        sourceTime,
        profile,
        heading,
        lanePose?.modelCenterResolved ?? false,
      )
    })
    this.pruneHistory(context.sequence, activeIds)
    this.motionBuffer.push({
      sequence: context.sequence,
      elapsedSeconds: context.elapsedSeconds,
      arrivalTimeMs: performance.now(),
      samples,
    })
    return { inputCount: vehicles.length, visibleCount: visible.length, radiusMeters }
  }

  refreshViewport(): VehicleRenderStats {
    return this.update(this.lastVehicles, this.lastContext, true)
  }

  clear(): void {
    this.lastVehicles = []
    this.lastContext = { sessionId: '', state: null, sequence: -1, elapsedSeconds: 0 }
    this.sessionId = ''
    this.resetRuntime()
  }

  private resetRuntime(): void {
    this.visibleCount = 0
    this.primed = false
    this.motionBuffer.reset()
    this.presentationClock.reset()
    this.selector.reset()
    this.poseHistory.clear()
    this.laneTrackHistory.clear()
    this.historyLastSeenSequence.clear()
    this.twin.reset()
  }

  private scheduleMotionFrame(): void {
    if (this.outputFrameId !== null) return
    this.outputFrameId = requestAnimationFrame((wallTimeMs) => {
      this.outputFrameId = null
      this.flushMotionFrame(wallTimeMs)
      this.scheduleMotionFrame()
    })
  }

  private flushMotionFrame(wallTimeMs: number): void {
    if (!isVehicleAnimationActive(this.lastContext.state)) return
    const samples = this.motionBuffer.sample(wallTimeMs)
    if (!samples?.length) return
    const time = this.presentationClock.next(Date.now())
    const timedSamples: VehicleTwinSample[] = samples.map((sample) => ({ ...sample, time }))
    if (!this.primed) {
      this.twin.push(timedSamples.map((sample) => ({
        ...sample,
        time: time - TWIN_INTERPOLATION_DELAY_MS,
      })))
      this.primed = true
    }
    this.twin.push(timedSamples)
    this.engine.requestRender()
  }

  private pruneHistory(sequence: number, activeIds: Set<string> = new Set()): void {
    if (!Number.isFinite(sequence) || sequence < 0) return
    for (const [id, lastSeenSequence] of this.historyLastSeenSequence) {
      if (activeIds.has(id) || sequence - lastSeenSequence <= VEHICLE_HISTORY_TTL_SNAPSHOTS) continue
      this.historyLastSeenSequence.delete(id)
      this.poseHistory.delete(id)
      this.laneTrackHistory.delete(id)
    }
  }

  destroy(): void {
    if (this.outputFrameId !== null) cancelAnimationFrame(this.outputFrameId)
    this.outputFrameId = null
    this.clear()
    this.engine.remove(this.twin)
  }
}
