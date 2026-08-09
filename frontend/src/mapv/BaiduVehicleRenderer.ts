import * as mapvthree from '@baidumap/mapv-three'
import type { SimulationLaneRuntime, SimulationState } from '../types/simulation'
import type { TrafficVehicleView } from '../types/traffic'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'
import type { RoadCoordinateProjector } from './roadGeometry'
import {
  AdaptiveVehicleRenderBudget,
  resolveVehicleRenderRadius,
  StableVehicleSelector,
  type VehicleRenderQuality,
} from './vehicleVisibility'
import {
  createVehicleTwinSample,
  type VehicleTwinSample,
} from './vehicleTwinSample'
import {
  ELECTRIC_BICYCLE_MODEL_TYPE,
  resolveVehicleModelProfile,
} from './vehicleModelProfiles.ts'
import {
  resolveStableVehicleHeading,
  sumoAngleToMapHeading,
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
import {
  minimumForwardTrackDistance,
  resolveCrossedStopLine,
  resolveVisualQueueConstraints,
  shouldAllowStopClamp,
  type VehiclePoseState,
} from './vehiclePoseStability'
import type { ResolvedLanePose } from './realistic/intersectionLaneHeading.ts'

const TWIN_INTERPOLATION_DELAY_MS = 32
const VEHICLE_HISTORY_TTL_SNAPSHOTS = 30
const EMPTY_RENDER_GRACE_SNAPSHOTS = 4
const LANE_RECOVERY_GRACE_SNAPSHOTS = 3
const NORMAL_OUTPUT_FRAME_MS = 1_000 / 30
const CONSTRAINED_OUTPUT_FRAME_MS = 1_000 / 20

export interface VehicleRenderContext {
  sessionId: string
  state: SimulationState | null
  sequence: number
  elapsedSeconds: number
  laneRuntimeById?: Record<string, SimulationLaneRuntime>
}

export interface VehicleRenderStats {
  inputCount: number
  visibleCount: number
  radiusMeters: number
  vehicleLimit: number
  quality: VehicleRenderQuality
  fps: number | null
  bufferSeconds: number
  sourceRate: number
  sourceGapP95Ms: number
  sourceGapP99Ms: number
  underrunCount: number
  underrunActive: boolean
  laneRecoveryCount: number
  temporarilyHiddenCount: number
  retainedMissingCount: number
  confirmedRemovedCount: number
  twinResetCount: number
}

export class BaiduVehicleRenderer {
  private readonly engine: mapvthree.Engine
  private readonly twin: mapvthree.Twin
  private readonly projector: RoadCoordinateProjector
  private readonly selector = new StableVehicleSelector()
  private readonly renderBudget = new AdaptiveVehicleRenderBudget()
  private readonly poseHistory = new Map<string, VehicleHeadingState>()
  private readonly poseStates = new Map<string, VehiclePoseState>()
  private readonly historyLastSeenSequence = new Map<string, number>()
  private readonly presentationClock = new VehiclePresentationClock()
  private readonly motionBuffer = new VehicleMotionBuffer()
  private outputFrameId: number | null = null
  private laneHeadingResolver: LaneHeadingResolver | null = null
  private lanePoseResolver: LanePoseResolver | null = null
  private lastVehicles: TrafficVehicleView[] = []
  private lastSourceSamples: VehicleTwinSample[] = []
  private lastContext: VehicleRenderContext = {
    sessionId: '',
    state: null,
    sequence: -1,
    elapsedSeconds: 0,
    laneRuntimeById: {},
  }
  private sessionId = ''
  private visibleCount = 0
  private primed = false
  private active = true
  private emptySourceSnapshotStreak = 0
  private lastRosterSequence = -1
  private lastOutputFrameMs: number | null = null
  private laneRecoveryCount = 0
  private temporarilyHiddenCount = 0
  private retainedMissingCount = 0
  private confirmedRemovedCount = 0
  private twinResetCount = 0

  constructor(
    engine: mapvthree.Engine,
    projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
  ) {
    this.engine = engine
    this.projector = projector
    const realisticModels = mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL as unknown as Record<string, string>
    this.twin = engine.add(new mapvthree.Twin({
      delay: TWIN_INTERPOLATION_DELAY_MS,
      modelConfig: {
        3: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.CAR,
        6: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.BUS,
        10: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.TRUCK,
        [ELECTRIC_BICYCLE_MODEL_TYPE]: realisticModels.ELECTRICBICYCLE,
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
    this.poseStates.clear()
    this.historyLastSeenSequence.clear()
  }

  update(
    vehicles: TrafficVehicleView[],
    context: VehicleRenderContext,
    force = false,
  ): VehicleRenderStats {
    this.laneRecoveryCount = 0
    this.temporarilyHiddenCount = 0
    this.retainedMissingCount = 0
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
      return this.stats(vehicles.length, radiusMeters)
    }
    this.lastVehicles = vehicles
    this.lastContext = context
    if (!this.active) return this.stats(vehicles.length, radiusMeters)
    if (context.state === 'PAUSED' || previousState === 'PAUSED') this.motionBuffer.pause()
    if (
      vehicles.length === 0
      && (context.state === 'STOPPED' || context.state === 'COMPLETED' || context.state === 'FAILED')
    ) {
      this.resetRuntime()
      return this.stats(0, radiusMeters)
    }
    const visible = this.selector.select(
      vehicles,
      this.projector,
      this.engine.map.getCenter(),
      cameraRange,
      snapshotKey,
      this.renderBudget.state().limit,
    )
    this.visibleCount = visible.length
    if (visible.length === 0) {
      this.lastSourceSamples = []
      this.motionBuffer.push({
        sequence: context.sequence,
        elapsedSeconds: context.elapsedSeconds,
        arrivalTimeMs: performance.now(),
        samples: [],
      })
      this.recordSourceRoster(context.sequence, 0)
      this.pruneHistory(context.sequence)
      return this.stats(vehicles.length, radiusMeters)
    }
    const sourceTime = context.elapsedSeconds * 1_000
    const activeIds = new Set<string>()
    const drafts = visible.map(({ vehicle, longitude, latitude }) => {
      const previousHeading = this.poseHistory.get(vehicle.vehicle_id)
      const previousPose = this.poseStates.get(vehicle.vehicle_id) ?? null
      const profile = resolveVehicleModelProfile(vehicle.type_id)
      const laneHeading = this.laneHeadingResolver?.(vehicle.lane_id, vehicle.lane_position) ?? null
      const minimumTrackDistance = minimumForwardTrackDistance(previousPose, vehicle)
      const resolverOptions = {
        speedMetersPerSecond: vehicle.speed,
        expectedHeading: previousHeading?.reliableHeading ?? sumoAngleToMapHeading(vehicle.angle),
        laneRuntime: context.laneRuntimeById?.[vehicle.lane_id] ?? null,
        previousTrackProgress: previousPose?.trackProgress,
        allowStopClamp: shouldAllowStopClamp(previousPose, vehicle),
        minimumModelCenterDistanceMeters: minimumTrackDistance,
      }
      const lanePose = this.lanePoseResolver?.(
        vehicle.lane_id,
        [longitude, latitude],
        profile.targetLengthMeters / 2,
        previousPose?.laneId,
        previousPose?.trackKey,
        resolverOptions,
      )
      if (!lanePose && laneHeading != null) {
        this.laneRecoveryCount += 1
        if (previousPose && previousPose.laneResolutionFailures < LANE_RECOVERY_GRACE_SNAPSHOTS) {
          this.retainedMissingCount += 1
          return {
            vehicle,
            profile,
            previousHeading,
            previousPose,
            laneHeading,
            lanePose: null,
            resolverOptions,
            sourceLongitude: longitude,
            sourceLatitude: latitude,
            longitude: previousPose.longitude,
            latitude: previousPose.latitude,
            heldForLaneRecovery: true,
          }
        }
        this.temporarilyHiddenCount += 1
        return null
      }
      return {
        vehicle,
        profile,
        previousHeading,
        previousPose,
        laneHeading,
        lanePose: lanePose ?? null,
        resolverOptions,
        sourceLongitude: longitude,
        sourceLatitude: latitude,
        longitude: lanePose?.longitude ?? longitude,
        latitude: lanePose?.latitude ?? latitude,
        heldForLaneRecovery: false,
      }
    }).filter((draft): draft is NonNullable<typeof draft> => draft !== null)

    const queueConstraints = resolveVisualQueueConstraints(drafts.flatMap((draft) => (
      draft.lanePose
        ? [{
            id: draft.vehicle.vehicle_id,
            trackKey: draft.lanePose.trackKey,
            lanePosition: draft.vehicle.lane_position,
            naturalCenterDistanceMeters: draft.lanePose.modelCenterDistanceMeters,
            lengthMeters: draft.profile.targetLengthMeters,
            previousCenterDistanceMeters: draft.previousPose?.trackKey === draft.lanePose.trackKey
              ? draft.previousPose.trackDistanceMeters
              : undefined,
          }]
        : []
    )))

    const samples = drafts.flatMap((draft) => {
      const { vehicle, profile, previousHeading, previousPose } = draft
      const queueConstraint = queueConstraints.get(vehicle.vehicle_id)
      if (queueConstraint?.hidden) return []
      let lanePose: ResolvedLanePose | null = draft.lanePose
      if (
        lanePose
        && queueConstraint?.maximumCenterDistanceMeters != null
        && lanePose.modelCenterDistanceMeters > queueConstraint.maximumCenterDistanceMeters
      ) {
        const minimumDistance = draft.resolverOptions.minimumModelCenterDistanceMeters
        if (
          minimumDistance != null
          && queueConstraint.maximumCenterDistanceMeters < minimumDistance
        ) return []
        lanePose = this.lanePoseResolver?.(
          vehicle.lane_id,
          [draft.sourceLongitude, draft.sourceLatitude],
          profile.targetLengthMeters / 2,
          previousPose?.laneId,
          previousPose?.trackKey,
          {
            ...draft.resolverOptions,
            minimumModelCenterDistanceMeters: undefined,
            maximumModelCenterDistanceMeters: queueConstraint.maximumCenterDistanceMeters,
          },
        ) ?? null
        if (!lanePose) return []
      }
      const renderLongitude = lanePose?.longitude ?? draft.longitude
      const renderLatitude = lanePose?.latitude ?? draft.latitude
      const point = { longitude: renderLongitude, latitude: renderLatitude }
      const resolved = resolveStableVehicleHeading({
        sumoAngleDegrees: vehicle.angle,
        speedMetersPerSecond: vehicle.speed,
        current: point,
        timeSeconds: context.elapsedSeconds,
        laneHeading: lanePose?.heading
          ?? draft.laneHeading,
      }, previousHeading ?? null)
      const heading = resolved.heading
      this.poseHistory.set(vehicle.vehicle_id, resolved.state)
      this.historyLastSeenSequence.set(vehicle.vehicle_id, context.sequence)
      this.poseStates.set(vehicle.vehicle_id, {
        backendDistance: vehicle.distance,
        routeId: vehicle.route_id,
        routeIndex: vehicle.route_index,
        laneId: vehicle.lane_id,
        trackKey: lanePose?.trackKey,
        trackProgress: lanePose?.trackProgress,
        trackDistanceMeters: lanePose?.modelCenterDistanceMeters,
        crossedStopLine: lanePose
          ? resolveCrossedStopLine(
              previousPose,
              vehicle,
              lanePose.naturalFrontDistanceMeters,
              lanePose.stopFrontLimitDistanceMeters,
              lanePose.stopClamped,
            )
          : previousPose?.crossedStopLine ?? false,
        laneResolutionFailures: draft.heldForLaneRecovery
          ? previousPose?.lastSeenSequence === context.sequence
            ? previousPose.laneResolutionFailures
            : (previousPose?.laneResolutionFailures ?? 0) + 1
          : 0,
        longitude: renderLongitude,
        latitude: renderLatitude,
        heading,
        lastSeenSequence: context.sequence,
      })
      activeIds.add(vehicle.vehicle_id)
      return [createVehicleTwinSample(
        vehicle,
        renderLongitude,
        renderLatitude,
        sourceTime,
        profile,
        heading,
        lanePose?.modelCenterResolved ?? false,
      )]
    })
    this.lastSourceSamples = samples
    this.pruneHistory(context.sequence, activeIds)
    this.motionBuffer.push({
      sequence: context.sequence,
      elapsedSeconds: context.elapsedSeconds,
      arrivalTimeMs: performance.now(),
      samples,
    })
    this.recordSourceRoster(context.sequence, samples.length)
    if (!isVehicleAnimationActive(context.state) && samples.length > 0) {
      this.presentImmediate(samples)
    }
    return this.stats(vehicles.length, radiusMeters)
  }

  refreshViewport(): VehicleRenderStats {
    return this.update(this.lastVehicles, this.lastContext, true)
  }

  beginViewportTransition(): void {
    // Preserve models during a camera/resolver transaction to avoid a full-scene flash.
    this.selector.reset()
    this.motionBuffer.pause()
  }

  setActive(active: boolean): void {
    if (this.active === active) return
    this.active = active
    if (active) {
      const startableTwin = this.twin as mapvthree.Twin & { start?: () => void }
      startableTwin.start?.()
      this.motionBuffer.reset()
      this.update(this.lastVehicles, this.lastContext, true)
      this.scheduleMotionFrame()
      if (this.lastSourceSamples.length > 0) this.presentImmediate(this.lastSourceSamples)
    } else {
      if (this.outputFrameId !== null) cancelAnimationFrame(this.outputFrameId)
      this.outputFrameId = null
      this.motionBuffer.pause()
      const pausableTwin = this.twin as mapvthree.Twin & { pause?: () => void }
      pausableTwin.pause?.()
    }
  }

  clear(): void {
    this.lastVehicles = []
    this.lastContext = {
      sessionId: '',
      state: null,
      sequence: -1,
      elapsedSeconds: 0,
      laneRuntimeById: {},
    }
    this.sessionId = ''
    this.resetRuntime()
  }

  private resetRuntime(): void {
    this.visibleCount = 0
    this.primed = false
    this.lastSourceSamples = []
    this.emptySourceSnapshotStreak = 0
    this.lastRosterSequence = -1
    this.lastOutputFrameMs = null
    this.motionBuffer.reset()
    this.presentationClock.reset()
    this.selector.reset()
    this.renderBudget.reset()
    this.poseHistory.clear()
    this.poseStates.clear()
    this.historyLastSeenSequence.clear()
    this.twin.reset()
    this.twinResetCount += 1
  }

  private scheduleMotionFrame(): void {
    if (!this.active || this.outputFrameId !== null) return
    this.outputFrameId = requestAnimationFrame((wallTimeMs) => {
      this.outputFrameId = null
      this.flushMotionFrame(wallTimeMs)
      this.scheduleMotionFrame()
    })
  }

  private flushMotionFrame(wallTimeMs: number): void {
    if (!isVehicleAnimationActive(this.lastContext.state)) return
    const budget = this.renderBudget.recordFrame(wallTimeMs)
    const frameInterval = budget.quality === 'constrained'
      ? CONSTRAINED_OUTPUT_FRAME_MS
      : NORMAL_OUTPUT_FRAME_MS
    if (
      this.lastOutputFrameMs != null
      && wallTimeMs - this.lastOutputFrameMs < frameInterval - 1
    ) return
    this.lastOutputFrameMs = wallTimeMs
    const samples = this.motionBuffer.sample(wallTimeMs)
    if (samples === null) return
    if (samples.length === 0) {
      if (this.primed && this.emptySourceSnapshotStreak > EMPTY_RENDER_GRACE_SNAPSHOTS) {
        this.twin.reset()
        this.twinResetCount += 1
        this.primed = false
        this.confirmedRemovedCount += 1
        this.engine.requestRender()
      }
      return
    }
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

  private recordSourceRoster(sequence: number, sampleCount: number): void {
    if (!Number.isFinite(sequence) || sequence === this.lastRosterSequence) return
    this.lastRosterSequence = sequence
    this.emptySourceSnapshotStreak = sampleCount === 0
      ? this.emptySourceSnapshotStreak + 1
      : 0
  }

  private presentImmediate(samples: VehicleTwinSample[]): void {
    const time = this.presentationClock.next(Date.now())
    const timedSamples = samples.map((sample) => ({ ...sample, time }))
    if (!this.primed) {
      this.twin.reset()
      this.twinResetCount += 1
    }
    this.twin.push(timedSamples.map((sample) => ({
      ...sample,
      time: time - TWIN_INTERPOLATION_DELAY_MS,
    })))
    this.twin.push(timedSamples)
    this.primed = true
    this.engine.requestRender()
  }

  private stats(inputCount: number, radiusMeters: number): VehicleRenderStats {
    const budget = this.renderBudget.state()
    const motion = this.motionBuffer.stats()
    return {
      inputCount,
      visibleCount: this.visibleCount,
      radiusMeters,
      vehicleLimit: budget.limit,
      quality: budget.quality,
      fps: budget.fps,
      bufferSeconds: motion.bufferSeconds,
      sourceRate: motion.sourceRate,
      sourceGapP95Ms: motion.sourceGapP95Ms,
      sourceGapP99Ms: motion.sourceGapP99Ms,
      underrunCount: motion.underrunCount,
      underrunActive: motion.underrunActive,
      laneRecoveryCount: this.laneRecoveryCount,
      temporarilyHiddenCount: this.temporarilyHiddenCount,
      retainedMissingCount: this.retainedMissingCount,
      confirmedRemovedCount: this.confirmedRemovedCount,
      twinResetCount: this.twinResetCount,
    }
  }

  private pruneHistory(sequence: number, activeIds: Set<string> = new Set()): void {
    if (!Number.isFinite(sequence) || sequence < 0) return
    for (const [id, lastSeenSequence] of this.historyLastSeenSequence) {
      if (activeIds.has(id) || sequence - lastSeenSequence <= VEHICLE_HISTORY_TTL_SNAPSHOTS) continue
      this.historyLastSeenSequence.delete(id)
      this.poseHistory.delete(id)
      this.poseStates.delete(id)
    }
  }

  destroy(): void {
    if (this.outputFrameId !== null) cancelAnimationFrame(this.outputFrameId)
    this.outputFrameId = null
    this.clear()
    this.engine.remove(this.twin)
  }
}
