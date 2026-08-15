import * as mapvthree from '@baidumap/mapv-three'
import type { SimulationLaneRuntime, SimulationState } from '../types/simulation'
import type { TrafficVehicleView } from '../types/traffic'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'
import type { RoadCoordinateProjector } from './roadGeometry'
import {
  AdaptiveVehicleRenderBudget,
  resolveVehicleRenderRadius,
  StableVehicleSelector,
  type VisibleVehicle,
  type VehicleRenderQuality,
} from './vehicleVisibility'
import {
  createVehicleTwinSample,
  unwrapVehicleModelDirection,
  type DynamicConnectionEvidence,
  type VehicleTwinSample,
} from './vehicleTwinSample'
import {
  ELECTRIC_BICYCLE_MODEL_TYPE,
  resolveVehicleModelProfile,
  type VehicleModelProfile,
} from './vehicleModelProfiles.ts'
import {
  moveFromFrontBumperToModelCenter,
  resolveStableVehicleHeading,
  shortestAngleDelta,
  type VehicleHeadingState,
} from './vehicleOrientation.ts'
import {
  SumoHeadingField,
  type SumoHeadingAnchor,
} from './sumoHeadingTransform.ts'
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
  classifyRoadTransition,
  resolveCrossedStopLine,
  reliableVehicleLanePosition,
  vehicleTelemetryIsPlaceholder,
  vehiclePoseDisplacementIsStable,
  vehiclePoseDisplacementMeters,
  type VehiclePoseState,
} from './vehiclePoseStability'
import type { ResolvedLanePose } from './realistic/intersectionLaneHeading.ts'
import type { RoadSurfaceVisibilityInterval } from './realistic/roadSurfaceExclusions.ts'
import { VehicleOutputPacer } from './vehicleOutputPacing.ts'
import {
  resolveBufferedLaneTransitionConnection,
  resolveVehicleRouteTurnResolution,
  type VehicleConnectionLock,
  type VehicleRouteTurnResolutionStatus,
} from './vehicleRouteTurnIndex.ts'

const TWIN_INTERPOLATION_DELAY_MS = 250
const TWIN_INITIAL_SAMPLE_SPACING_MS = 50
const VEHICLE_HISTORY_TTL_SNAPSHOTS = 30
const LANE_RECOVERY_HOLD_SECONDS = 1.5
const VIEWPORT_SNAPSHOT_HISTORY_SECONDS = 9
const MAX_VIEWPORT_STAGING_SNAPSHOTS = 32
export const NORMAL_TWIN_OUTPUT_FPS = 30
export const STABLE_TWIN_OUTPUT_FPS = 24
const NORMAL_OUTPUT_FRAME_MS = 1_000 / NORMAL_TWIN_OUTPUT_FPS
const CONSTRAINED_OUTPUT_FRAME_MS = 1_000 / STABLE_TWIN_OUTPUT_FPS

function laneRuntimeRequiresStop(runtime: SimulationLaneRuntime | null | undefined): boolean {
  const signalState = runtime?.signal_state?.toLowerCase() ?? ''
  return runtime?.lane_has_green === false
    || (runtime?.lane_has_green !== true && /^[ry]+$/.test(signalState))
}

export interface VehicleRenderContext {
  sessionId: string
  state: SimulationState | null
  sequence: number
  elapsedSeconds: number
  laneRuntimeById?: Record<string, SimulationLaneRuntime>
  trafficPeriod?: string
  intersectionId?: string
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
  twinSafetyMarginMs: number
  maximumTwinOutputGapMs: number
  emptyBufferInterceptCount: number
  terminalFreezeActive: boolean
  laneRecoveryVehicleIds: string[]
  temporarilyHiddenVehicleIds: string[]
  duplicateVehicleIds: string[]
  incompatiblePathInterpolationCount: number
  incompatiblePathInterpolationBlockedCount: number
  poseViolationCount: number
  targetBufferSeconds: number
  expectedPlaybackRate: number
  globalBufferDepthSeconds: number
  globalPlaybackRate: number
  globalUnderrunPauseSeconds: number
  authoritativeInterpolationCount: number
  visibleTeleportCount: number
  pathResetCount: number
  movingFreezeFrameCount: number
  batchArrivalCount: number
  twinGapFillFrameCount: number
  twinPlaybackBacklogMs: number
  vehicleScaleViolationCount: number
  normalTransitionEpochViolationCount: number
  offRoadVehicleCount: number
  stuckLaneChangeCount: number
  maximumRoadMappingErrorMeters: number
  routeHintHitCount: number
  routeHintMismatchCount: number
  ambiguousRouteCandidateRejectionCount: number
  ambiguousIncomingPendingCount: number
  staleConnectionReleaseCount: number
  connectionMismatchCount: number
  laneChangeCorridorViolationCount: number
  intermediateOffRoadFrameCount: number
  detailedAreaRawFallbackCount: number
  compiledSegmentCount: number
  rejectedCompiledSegmentCount: number
  endpointValidationFailureCount: number
  compiledReadyElapsedSeconds: number | null
  dynamicRuntimeVehicleCount: number
  bufferedLookaheadConnectionCount: number
  compiledSegmentCacheHitCount: number
  compiledSegmentCacheHitRate: number
  isolatedVehicleCount: number
  maximumIsolationSeconds: number
  recoveredVehicleCount: number
  ghostVehicleIds: string[]
  hiddenUnresolvedVehicleIds: string[]
  viewportPrecompileMilliseconds: number
  viewportTwinBlankFrameCount: number
  viewportFirstFrameVehicleCount: number
  surfaceExclusionVehicleFilterCount: number
  vehiclePoseDiagnostics: VehiclePoseDiagnostic[]
  displayElapsedSeconds: number | null
}

interface BufferedViewportVehicleSnapshot {
  vehicles: TrafficVehicleView[]
  context: VehicleRenderContext
}

export interface ViewportVehicleStagingBuffer {
  intersectionId: string
  snapshots: BufferedViewportVehicleSnapshot[]
  headingResolver: LaneHeadingResolver | null
  poseResolver: LanePoseResolver | null
  displayElapsedSeconds: number
  authoritativeLocalVehicleCount: number
  precompileMilliseconds: number
  firstFrameVehicleCount: number
  priorityVehicleIds: string[]
}
export type ViewportVehicleStage = ViewportVehicleStagingBuffer

export interface ViewportStagePreparationDiagnostic {
  reason: 'ready' | 'display_time_unavailable' | 'display_time_unbracketed' | 'local_poses_unresolved'
  intersectionId: string
  displayElapsedSeconds: number | null
  firstSnapshotElapsedSeconds: number | null
  latestSnapshotElapsedSeconds: number | null
  snapshotCount: number
  authoritativeLocalVehicleCount: number
  validLocalVehicleCount: number
}

export type VehicleCoverageKind = 'local_manifest' | 'supplemental_network' | 'outside'

export interface VehiclePoseDiagnostic {
  vehicleId: string
  laneId: string
  traciAngleDegrees: number
  sumoHeadingDegrees: number | null
  headingTransformAnchorIds: string[]
  headingTransformLocal: boolean | null
  laneHeadingDegrees: number | null
  twinDirectionDegrees: number | null
  finalModelHeadingDegrees: number | null
  headingErrorDegrees: number | null
  sumoLaneDeltaDegrees: number | null
  motionPathKey?: string
  connectionKey?: string
  routeHintStatus: VehicleRouteTurnResolutionStatus
  routeHintSource?: 'fixed_route_index' | 'live_topology'
  connectionLockStage?: VehicleConnectionLock['stage']
  segmentKey?: string
  lifecycle: VehiclePoseState['lifecycle']
  transitionKind?: VehiclePoseState['transitionKind']
  roadTransitionKind?: VehiclePoseState['roadTransitionKind']
  rejectionReason?: string
  sourceLongitude: number
  sourceLatitude: number
  mappedLongitude: number
  mappedLatitude: number
  sourceArcDistanceMeters?: number
  sourceLateralOffsetMeters?: number
  roadMappingErrorMeters?: number
  mappingMode?: ResolvedLanePose['mappingMode']
  coverageKind: VehicleCoverageKind
}

export class BaiduVehicleRenderer {
  private readonly engine: mapvthree.Engine
  private readonly twin: mapvthree.Twin
  private readonly projector: RoadCoordinateProjector
  private readonly onDisplayElapsedSeconds?: (elapsedSeconds: number) => void
  private readonly selector = new StableVehicleSelector()
  private readonly renderBudget = new AdaptiveVehicleRenderBudget()
  private readonly poseHistory = new Map<string, VehicleHeadingState>()
  private readonly poseStates = new Map<string, VehiclePoseState>()
  private readonly twinDirectionByVehicleId = new Map<string, number>()
  private readonly historyLastSeenSequence = new Map<string, number>()
  private readonly pendingPoseCandidates = new Map<string, {
    point: { longitude: number; latitude: number }
    laneId: string
    occupancyKey?: string
    motionPathKey?: string
    segmentKey?: string
    stableSamples: number
    recovery: boolean
    sequence: number
  }>()
  private readonly pendingLaneChanges = new Map<string, {
    laneId: string
    stableSamples: number
    sequence: number
    startedSequence: number
  }>()
  private readonly routeCursorByVehicleId = new Map<string, number>()
  private readonly connectionLocksByVehicleId = new Map<string, VehicleConnectionLock>()
  private readonly invalidPoseSuppressedVehicles = new Set<string>()
  private readonly invalidPoseReentrySnapshots = new Map<string, { count: number; sequence: number }>()
  private readonly presentationClock = new VehiclePresentationClock()
  private readonly motionBuffer = new VehicleMotionBuffer()
  private readonly outputPacer = new VehicleOutputPacer()
  private readonly headingField: SumoHeadingField
  private outputFrameId: number | null = null
  private laneHeadingResolver: LaneHeadingResolver | null = null
  private lanePoseResolver: LanePoseResolver | null = null
  private lastVehicles: TrafficVehicleView[] = []
  private lastContext: VehicleRenderContext = {
    sessionId: '',
    state: null,
    sequence: -1,
    elapsedSeconds: 0,
    laneRuntimeById: {},
    trafficPeriod: '',
    intersectionId: '',
  }
  private sessionId = ''
  private visibleCount = 0
  private primed = false
  private active = true
  private emptySourceSnapshotStreak = 0
  private lastRosterSequence = -1
  private twinPlaybackBacklogMs = 0
  private stableOutputMode = false
  private laneRecoveryCount = 0
  private temporarilyHiddenCount = 0
  private retainedMissingCount = 0
  private confirmedRemovedCount = 0
  private twinResetCount = 0
  private maximumTwinOutputGapMs = 0
  private emptyBufferInterceptCount = 0
  private terminalFreezeActive = false
  private lastTwinPushWallTimeMs: number | null = null
  private twinGapFillFrameCount = 0
  private laneRecoveryVehicleIds = new Set<string>()
  private temporarilyHiddenVehicleIds = new Set<string>()
  private duplicateVehicleIds = new Set<string>()
  private vehiclePoseDiagnostics: VehiclePoseDiagnostic[] = []
  private offRoadVehicleCount = 0
  private stuckLaneChangeCount = 0
  private maximumRoadMappingErrorMeters = 0
  private routeHintHitCount = 0
  private routeHintMismatchCount = 0
  private ambiguousRouteCandidateRejectionCount = 0
  private ambiguousIncomingPendingCount = 0
  private staleConnectionReleaseCount = 0
  private connectionMismatchCount = 0
  private detailedAreaRawFallbackCount = 0
  private dynamicRuntimeVehicleCount = 0
  private bufferedLookaheadConnectionCount = 0
  private readonly viewportSnapshots: BufferedViewportVehicleSnapshot[] = []
  private replayingViewportSnapshots = false
  private viewportPrecompileMilliseconds = 0
  private viewportTwinBlankFrameCount = 0
  private viewportFirstFrameVehicleCount = 0
  private surfaceExclusionVehicleFilterCount = 0
  private sceneGeneration = 0
  private viewportTransitionActive = false
  private sharedDisplayElapsedSeconds: number | null = null
  private viewportStagePreparationDiagnostic: ViewportStagePreparationDiagnostic | null = null
  private viewportReplayPriorityVehicleIds: ReadonlySet<string> | null = null

  constructor(
    engine: mapvthree.Engine,
    projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
    onDisplayElapsedSeconds?: (elapsedSeconds: number) => void,
  ) {
    this.engine = engine
    this.projector = projector
    this.onDisplayElapsedSeconds = onDisplayElapsedSeconds
    this.headingField = new SumoHeadingField(projector)
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
  }

  setLaneHeadingResolver(resolver: LaneHeadingResolver | null): void {
    this.laneHeadingResolver = resolver
  }

  setStableMode(stable: boolean): void {
    if (this.stableOutputMode !== stable) this.outputPacer.reset()
    this.stableOutputMode = stable
  }

  setPresentationElapsedSeconds(elapsedSeconds: number | null): void {
    this.sharedDisplayElapsedSeconds = Number.isFinite(elapsedSeconds)
      ? Number(elapsedSeconds)
      : null
  }

  debugStats(): VehicleRenderStats {
    const radiusMeters = resolveVehicleRenderRadius(this.engine.map.getRange())
    return this.stats(this.lastVehicles.length, radiusMeters)
  }

  viewportStageDiagnostic(): ViewportStagePreparationDiagnostic | null {
    return this.viewportStagePreparationDiagnostic
      ? { ...this.viewportStagePreparationDiagnostic }
      : null
  }

  setHeadingAnchors(anchors: readonly SumoHeadingAnchor[]): void {
    this.headingField.setAnchors(anchors)
  }

  setLanePoseResolver(resolver: LanePoseResolver | null): void {
    this.lanePoseResolver = resolver
    this.motionBuffer.setMotionPathSampler(resolver?.motionPathSampler ?? null)
    this.poseHistory.clear()
    this.poseStates.clear()
    this.twinDirectionByVehicleId.clear()
    this.historyLastSeenSequence.clear()
    this.pendingLaneChanges.clear()
    this.connectionLocksByVehicleId.clear()
  }

  prepareViewportTransition(
    intersectionId: string,
    headingResolver: LaneHeadingResolver | null,
    poseResolver: LanePoseResolver | null,
  ): ViewportVehicleStagingBuffer | null {
    const startedAt = performance.now()
    const displayElapsedSeconds = this.sharedDisplayElapsedSeconds
    if (displayElapsedSeconds == null || this.viewportSnapshots.length < 2) {
      if (this.lastContext.sessionId && isVehicleAnimationActive(this.lastContext.state)) {
        this.viewportStagePreparationDiagnostic = {
          reason: 'display_time_unavailable',
          intersectionId,
          displayElapsedSeconds,
          firstSnapshotElapsedSeconds: this.viewportSnapshots[0]?.context.elapsedSeconds ?? null,
          latestSnapshotElapsedSeconds: this.viewportSnapshots.at(-1)?.context.elapsedSeconds ?? null,
          snapshotCount: this.viewportSnapshots.length,
          authoritativeLocalVehicleCount: 0,
          validLocalVehicleCount: 0,
        }
        return null
      }
      return {
        intersectionId,
        snapshots: [...this.viewportSnapshots],
        headingResolver,
        poseResolver,
        displayElapsedSeconds: displayElapsedSeconds ?? this.lastContext.elapsedSeconds,
        authoritativeLocalVehicleCount: 0,
        precompileMilliseconds: performance.now() - startedAt,
        firstFrameVehicleCount: 0,
        priorityVehicleIds: [],
      }
    }
    const snapshots = [...this.viewportSnapshots]
    const rightIndex = snapshots.findIndex((entry) => (
      entry.context.elapsedSeconds > displayElapsedSeconds
    ))
    if (rightIndex <= 0) {
      this.viewportStagePreparationDiagnostic = {
        reason: 'display_time_unbracketed',
        intersectionId,
        displayElapsedSeconds,
        firstSnapshotElapsedSeconds: snapshots[0]?.context.elapsedSeconds ?? null,
        latestSnapshotElapsedSeconds: snapshots.at(-1)?.context.elapsedSeconds ?? null,
        snapshotCount: snapshots.length,
        authoritativeLocalVehicleCount: 0,
        validLocalVehicleCount: 0,
      }
      return null
    }
    const left = snapshots[rightIndex - 1]
    const right = snapshots[rightIndex]
    if (!left || !right) return null
    const authoritativeLocalVehicles = left.vehicles.filter((vehicle) => {
      if (vehicle.longitude == null || vehicle.latitude == null || !poseResolver) return false
      const coordinate = this.projector([vehicle.longitude, vehicle.latitude, 0])
      return poseResolver.hasLane(vehicle.lane_id)
        || poseResolver.coversDetailedArea([coordinate[0], coordinate[1]])
    })
    const validLocalVehicles = authoritativeLocalVehicles.filter((vehicle) => {
      if (vehicle.longitude == null || vehicle.latitude == null || !poseResolver) return false
      const coordinate = this.projector([vehicle.longitude, vehicle.latitude, 0])
      const profile = resolveVehicleModelProfile(vehicle.type_id)
      if (!poseResolver.hasLane(vehicle.lane_id)) return true
      return Boolean(poseResolver(
          vehicle.lane_id,
          [coordinate[0], coordinate[1]],
          profile.targetLengthMeters / 2,
        ))
    })
    const rightVehiclesById = new Map(right.vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle] as const))
    const priorityVehicleIds = validLocalVehicles.flatMap((vehicle) => {
      const rightVehicle = rightVehiclesById.get(vehicle.vehicle_id)
      if (!rightVehicle || rightVehicle.longitude == null || rightVehicle.latitude == null || !poseResolver) return []
      const rightCoordinate = this.projector([rightVehicle.longitude, rightVehicle.latitude, 0])
      if (!poseResolver.hasLane(rightVehicle.lane_id)) return [vehicle.vehicle_id]
      const profile = resolveVehicleModelProfile(rightVehicle.type_id)
      return poseResolver(
        rightVehicle.lane_id,
        [rightCoordinate[0], rightCoordinate[1]],
        profile.targetLengthMeters / 2,
      ) ? [vehicle.vehicle_id] : []
    })
    if (authoritativeLocalVehicles.length > 0 && validLocalVehicles.length === 0) {
      this.viewportStagePreparationDiagnostic = {
        reason: 'local_poses_unresolved',
        intersectionId,
        displayElapsedSeconds,
        firstSnapshotElapsedSeconds: snapshots[0]?.context.elapsedSeconds ?? null,
        latestSnapshotElapsedSeconds: snapshots.at(-1)?.context.elapsedSeconds ?? null,
        snapshotCount: snapshots.length,
        authoritativeLocalVehicleCount: authoritativeLocalVehicles.length,
        validLocalVehicleCount: 0,
      }
      return null
    }
    this.viewportStagePreparationDiagnostic = {
      reason: 'ready',
      intersectionId,
      displayElapsedSeconds,
      firstSnapshotElapsedSeconds: snapshots[0]?.context.elapsedSeconds ?? null,
      latestSnapshotElapsedSeconds: snapshots.at(-1)?.context.elapsedSeconds ?? null,
      snapshotCount: snapshots.length,
      authoritativeLocalVehicleCount: authoritativeLocalVehicles.length,
      validLocalVehicleCount: validLocalVehicles.length,
    }
    return {
      intersectionId,
      snapshots,
      headingResolver,
      poseResolver,
      displayElapsedSeconds,
      authoritativeLocalVehicleCount: authoritativeLocalVehicles.length,
      precompileMilliseconds: performance.now() - startedAt,
      firstFrameVehicleCount: validLocalVehicles.length,
      priorityVehicleIds,
    }
  }

  commitViewportTransition(
    stage: ViewportVehicleStagingBuffer,
    hiddenRoadIds: Iterable<string> = [],
    surfaceVisibility: Iterable<RoadSurfaceVisibilityInterval> = [],
  ): boolean {
    const startedAt = performance.now()
    this.headingField.setPreferredIntersection(stage.intersectionId)
    this.laneHeadingResolver = stage.headingResolver
    this.lanePoseResolver = stage.poseResolver
    this.motionBuffer.setMotionPathSampler(stage.poseResolver?.motionPathSampler ?? null)
    this.motionBuffer.reset()
    this.selector.reset()
    this.outputPacer.reset()
    this.twinPlaybackBacklogMs = 0
    this.poseHistory.clear()
    this.poseStates.clear()
    this.twinDirectionByVehicleId.clear()
    this.pendingPoseCandidates.clear()
    this.pendingLaneChanges.clear()
    this.historyLastSeenSequence.clear()
    // Road mesh exclusions are visual-only. They must never suppress SUMO
    // vehicles or participate in the vehicle staging budget.
    void hiddenRoadIds
    void surfaceVisibility
    this.routeCursorByVehicleId.clear()
    this.connectionLocksByVehicleId.clear()
    this.invalidPoseSuppressedVehicles.clear()
    this.invalidPoseReentrySnapshots.clear()
    this.terminalFreezeActive = false
    this.viewportTransitionActive = false
    this.replayingViewportSnapshots = true
    this.viewportReplayPriorityVehicleIds = new Set(stage.priorityVehicleIds)
    const snapshots = stage.snapshots
    try {
      for (const snapshot of snapshots) {
        this.update(snapshot.vehicles, {
          ...snapshot.context,
          intersectionId: stage.intersectionId,
          laneRuntimeById: {},
        }, true)
      }
    } finally {
      this.replayingViewportSnapshots = false
      this.viewportReplayPriorityVehicleIds = null
    }
    const primingSamples = this.motionBuffer.sample(
      performance.now(),
      stage.displayElapsedSeconds,
    )
    if (stage.authoritativeLocalVehicleCount > 0 && !primingSamples?.length) {
      const motion = this.motionBuffer.stats()
      console.warn('[vehicle-stage] no validated first frame', JSON.stringify({
        intersectionId: stage.intersectionId,
        displayElapsedSeconds: stage.displayElapsedSeconds,
        authoritativeLocalVehicleCount: stage.authoritativeLocalVehicleCount,
        latestMappedSourceCount: this.visibleCount,
        compiledSegmentCount: motion.compiledSegmentCount,
        rejectedCompiledSegmentCount: motion.rejectedCompiledSegmentCount,
        endpointValidationFailureCount: motion.endpointValidationFailureCount,
        hiddenUnresolvedVehicleCount: motion.hiddenUnresolvedVehicleIds.length,
        ghostVehicleCount: motion.ghostVehicleIds.length,
      }))
      this.viewportTransitionActive = false
      this.viewportTwinBlankFrameCount += 1
      return false
    }
    this.twin.reset()
    this.twinResetCount += 1
    this.primed = true
    if (primingSamples?.length) this.presentImmediate(primingSamples)
    else this.primed = false
    this.viewportPrecompileMilliseconds = stage.precompileMilliseconds
      + performance.now() - startedAt
    this.viewportFirstFrameVehicleCount = primingSamples?.length ?? 0
    this.syncMotionFrameScheduling()
    return true
  }

  cancelViewportTransition(): void {
    if (!this.viewportTransitionActive) return
    this.viewportTransitionActive = false
    this.outputPacer.reset()
    this.twinPlaybackBacklogMs = 0
    this.syncMotionFrameScheduling()
  }

  update(
    vehicles: TrafficVehicleView[],
    context: VehicleRenderContext,
    force = false,
  ): VehicleRenderStats {
    this.laneRecoveryCount = 0
    this.temporarilyHiddenCount = 0
    this.retainedMissingCount = 0
    this.laneRecoveryVehicleIds.clear()
    this.temporarilyHiddenVehicleIds.clear()
    this.duplicateVehicleIds.clear()
    this.vehiclePoseDiagnostics = []
    this.offRoadVehicleCount = 0
    this.stuckLaneChangeCount = 0
    this.maximumRoadMappingErrorMeters = 0
    this.routeHintHitCount = 0
    this.routeHintMismatchCount = 0
    this.ambiguousRouteCandidateRejectionCount = 0
    this.ambiguousIncomingPendingCount = 0
    this.staleConnectionReleaseCount = 0
    this.connectionMismatchCount = 0
    this.detailedAreaRawFallbackCount = 0
    this.dynamicRuntimeVehicleCount = 0
    this.bufferedLookaheadConnectionCount = 0
    const previousSnapshotKey = `${this.lastContext.sessionId}:${this.lastContext.sequence}`
    const previousState = this.lastContext.state
    if (this.sessionId && context.sessionId && this.sessionId !== context.sessionId) {
      this.resetRuntime()
    }
    this.sessionId = context.sessionId
    if (!this.replayingViewportSnapshots) this.recordViewportSnapshot(vehicles, context)
    const cameraRange = this.engine.map.getRange()
    const radiusMeters = resolveVehicleRenderRadius(cameraRange)
    if (this.viewportTransitionActive && !this.replayingViewportSnapshots) {
      return this.stats(vehicles.length, radiusMeters)
    }
    const snapshotKey = `${context.sessionId}:${context.sequence}`
    const sameSession = context.sessionId === this.lastContext.sessionId
    const stateChanged = context.state !== this.lastContext.state
    const staleSnapshot = sameSession && (
      context.sequence < this.lastContext.sequence
      || (
        context.sequence === this.lastContext.sequence
        && context.elapsedSeconds <= this.lastContext.elapsedSeconds
        && !stateChanged
      )
    )
    if (!force && ((snapshotKey === previousSnapshotKey && !stateChanged) || staleSnapshot)) {
      return this.stats(vehicles.length, radiusMeters)
    }
    this.lastVehicles = vehicles
    this.lastContext = context
    this.syncMotionFrameScheduling()
    if (!this.active) return this.stats(vehicles.length, radiusMeters)
    if (context.state === 'PAUSED' || previousState === 'PAUSED') this.motionBuffer.pause()
    const terminalState = context.state === 'STOPPED'
      || context.state === 'COMPLETED'
      || context.state === 'FAILED'
    if (!terminalState && this.terminalFreezeActive) {
      this.terminalFreezeActive = false
      const startableTwin = this.twin as mapvthree.Twin & { start?: () => void }
      startableTwin.start?.()
    }
    if (vehicles.length === 0 && terminalState) {
      this.twin.reset()
      this.twinResetCount += 1
      this.primed = false
      this.visibleCount = 0
      return this.stats(0, radiusMeters)
    }
    const renderableVehicles = vehicles
    const visible = this.replayingViewportSnapshots
      ? this.selectViewportReplayVehicles(renderableVehicles, this.renderBudget.state().limit)
      : this.selector.select(
          renderableVehicles,
          this.projector,
          this.engine.map.getCenter(),
          cameraRange,
          snapshotKey,
          this.renderBudget.state().limit,
          this.lanePoseResolver
            ? (item) => Boolean(this.lanePoseResolver?.coversDetailedArea([
                item.longitude,
                item.latitude,
              ]))
            : undefined,
        )
    const sourceTime = context.elapsedSeconds * 1_000
    const activeIds = new Set<string>()
    const drafts = visible.map(({ vehicle, longitude, latitude }) => {
      const previousHeading = this.poseHistory.get(vehicle.vehicle_id)
      const previousPose = this.poseStates.get(vehicle.vehicle_id) ?? null
      const profile = resolveVehicleModelProfile(vehicle.type_id)
      const telemetryReliable = !vehicleTelemetryIsPlaceholder(vehicle)
      const lanePosition = reliableVehicleLanePosition(vehicle)
      const headingResolution = this.headingField.resolve(
        vehicle.angle,
        [longitude, latitude],
      )
      const sourceMapHeading = headingResolution?.heading ?? null
      const fallbackLaneHeading = this.laneHeadingResolver?.(vehicle.lane_id, lanePosition) ?? null
      const laneChangeStarted = Boolean(
        previousPose
        && previousPose.roadId === vehicle.road_id
        && previousPose.laneId !== vehicle.lane_id
        && !vehicle.road_id.startsWith(':'),
      )
      const pendingLaneChange = this.pendingLaneChanges.get(vehicle.vehicle_id)
      let laneChanging = laneChangeStarted
        || Boolean(pendingLaneChange && pendingLaneChange.laneId === vehicle.lane_id)
      let routeTurnResolution = resolveVehicleRouteTurnResolution(
        vehicle,
        context.trafficPeriod,
        context.intersectionId,
        this.connectionLocksByVehicleId.get(vehicle.vehicle_id),
        this.routeCursorByVehicleId.get(vehicle.vehicle_id),
      )
      if (
        routeTurnResolution.status !== 'hit'
        && previousPose
        && previousPose.laneId !== vehicle.lane_id
        && context.intersectionId
      ) {
        const bufferedConnection = resolveBufferedLaneTransitionConnection(
          context.intersectionId,
          previousPose.roadId,
          previousPose.laneId,
          vehicle.road_id,
          vehicle.lane_id,
        )
        if (bufferedConnection) {
          routeTurnResolution = {
            status: 'hit',
            candidateCount: 1,
            routeCursor: this.routeCursorByVehicleId.get(vehicle.vehicle_id),
            releasedStaleLock: Boolean(
              this.connectionLocksByVehicleId.get(vehicle.vehicle_id)?.connectionKey
              && this.connectionLocksByVehicleId.get(vehicle.vehicle_id)?.connectionKey
                !== bufferedConnection.connectionKey,
            ),
            connectionLock: {
              stage: 'exiting',
              connectionKey: bufferedConnection.connectionKey,
              motionPathKey: bufferedConnection.motionPathKey,
              fromLaneId: bufferedConnection.fromLaneId,
              toLaneId: bufferedConnection.toLaneId,
              viaLaneIds: [...bufferedConnection.viaLaneIds],
              source: 'live_topology',
            },
            hint: {
              period: null,
              flowId: vehicle.vehicle_id,
              routeIndex: -1,
              fromEdge: bufferedConnection.fromEdge,
              toEdge: bufferedConnection.toEdge,
              intersectionId: bufferedConnection.intersectionId,
              connectionKey: bufferedConnection.connectionKey,
              motionPathKey: bufferedConnection.motionPathKey,
              source: 'live_topology',
            },
          }
        }
      }
      if (routeTurnResolution.connectionLock.connectionKey) {
        this.connectionLocksByVehicleId.set(
          vehicle.vehicle_id,
          routeTurnResolution.connectionLock,
        )
      } else {
        this.connectionLocksByVehicleId.delete(vehicle.vehicle_id)
      }
      if (routeTurnResolution.releasedStaleLock) this.staleConnectionReleaseCount += 1
      if (routeTurnResolution.routeCursor != null) {
        const previousCursor = this.routeCursorByVehicleId.get(vehicle.vehicle_id) ?? -1
        this.routeCursorByVehicleId.set(
          vehicle.vehicle_id,
          Math.max(previousCursor, routeTurnResolution.routeCursor),
        )
      }
      if (routeTurnResolution.status === 'hit') this.routeHintHitCount += 1
      else if (routeTurnResolution.status === 'mismatch') this.routeHintMismatchCount += 1
      else if (routeTurnResolution.status === 'ambiguous') {
        this.ambiguousRouteCandidateRejectionCount += 1
      } else if (routeTurnResolution.status === 'pending') {
        this.ambiguousIncomingPendingCount += 1
      }
      if (routeTurnResolution.status === 'mismatch') this.connectionMismatchCount += 1
      const resolverOptions = {
        speedMetersPerSecond: vehicle.speed,
        expectedHeading: sourceMapHeading,
        previousTrackProgress: previousPose?.trackProgress,
        preserveSourceLateralOffset: true,
        preferredMotionPathKey: routeTurnResolution.hint?.motionPathKey,
        requirePreferredMotionPath: routeTurnResolution.status === 'hit',
        routeHintRejected: routeTurnResolution.status === 'mismatch'
          || routeTurnResolution.status === 'ambiguous'
          || routeTurnResolution.reason === 'outgoing_lane_requires_confirmed_connection_lock',
        vehicleHalfWidthMeters: profile.targetWidthMeters / 2,
        vehicleHalfLengthMeters: profile.targetLengthMeters / 2,
      }
      let lanePose = this.lanePoseResolver?.(
        vehicle.lane_id,
        [longitude, latitude],
        profile.targetLengthMeters / 2,
        previousPose?.laneId,
        previousPose?.trackKey,
        resolverOptions,
      ) ?? null
      if (laneChanging) {
        const centered = lanePose
          ? lanePose.sourceDistanceToLaneCenterMeters <= 0.35
          : false
        const stableSamples = centered
          && pendingLaneChange?.laneId === vehicle.lane_id
          && pendingLaneChange.sequence !== context.sequence
          ? pendingLaneChange.stableSamples + 1
          : centered ? 1 : 0
        if (stableSamples >= 2) {
          laneChanging = false
          this.pendingLaneChanges.delete(vehicle.vehicle_id)
        } else {
          const startedSequence = pendingLaneChange?.startedSequence ?? context.sequence
          this.pendingLaneChanges.set(vehicle.vehicle_id, {
            laneId: vehicle.lane_id,
            stableSamples,
            sequence: context.sequence,
            startedSequence,
          })
          if (context.sequence - startedSequence >= 6) this.stuckLaneChangeCount += 1
        }
      } else {
        this.pendingLaneChanges.delete(vehicle.vehicle_id)
      }
      let recoveredLanePose = false
      if (!lanePose && !laneChanging && this.lanePoseResolver) {
        lanePose = this.lanePoseResolver(
          vehicle.lane_id,
          [longitude, latitude],
          profile.targetLengthMeters / 2,
          previousPose?.laneId,
          previousPose?.trackKey,
          {
            ...resolverOptions,
            maximumSnapDistanceMeters: 8,
            relaxedTrackContinuity: true,
            preserveSourceLateralOffset: true,
          },
        )
        recoveredLanePose = lanePose !== null
      }
      if (recoveredLanePose && lanePose) {
        this.laneRecoveryCount += 1
        this.laneRecoveryVehicleIds.add(vehicle.vehicle_id)
        const pending = this.pendingPoseCandidates.get(vehicle.vehicle_id)
        const pendingMatches = pending
          && pending.recovery
          && pending.laneId === vehicle.lane_id
          && pending.motionPathKey === lanePose.motionPathKey
          && pending.segmentKey === lanePose.segmentKey
          && pending.sequence !== context.sequence
          && vehiclePoseDisplacementMeters(pending.point, lanePose) <= 6
        const stableSamples = pendingMatches ? pending.stableSamples + 1 : 1
        this.pendingPoseCandidates.set(vehicle.vehicle_id, {
          point: lanePose,
          laneId: vehicle.lane_id,
          occupancyKey: lanePose.occupancyKey,
          motionPathKey: lanePose.motionPathKey,
          segmentKey: lanePose.segmentKey,
          stableSamples,
          recovery: true,
          sequence: context.sequence,
        })
        if (stableSamples < 2) lanePose = null
        else this.pendingPoseCandidates.delete(vehicle.vehicle_id)
      }
      const incompatibleTransition = Boolean(
        !lanePose
        && !laneChanging
        && previousPose
        && previousPose.laneId !== vehicle.lane_id,
      )
      if (!lanePose && (recoveredLanePose || incompatibleTransition)) {
        if (!recoveredLanePose) {
          this.laneRecoveryCount += 1
          this.laneRecoveryVehicleIds.add(vehicle.vehicle_id)
        }
        if (
          previousPose
          && context.elapsedSeconds - previousPose.lastStableElapsedSeconds <= LANE_RECOVERY_HOLD_SECONDS
        ) {
          return {
            vehicle,
            profile,
            previousHeading,
            previousPose,
            laneHeading: null,
            lanePose: null,
            resolverOptions,
            sourceLongitude: longitude,
            sourceLatitude: latitude,
            longitude: previousPose.longitude,
            latitude: previousPose.latitude,
            telemetryReliable,
            sourceMapHeading,
            headingResolution,
            heldForLaneRecovery: true,
            laneChanging: false,
            rawFallback: false,
            rejectionReason: recoveredLanePose ? 'recovery_pending' : 'incompatible_topology_transition',
            routeTurnResolution,
          }
        }
      }
      const rawFallback = lanePose === null && !laneChanging
      const rejectedInsideDetailedLane = Boolean(
        !lanePose
        && this.lanePoseResolver?.hasLane(vehicle.lane_id),
      )
      if (lanePose) {
        this.maximumRoadMappingErrorMeters = Math.max(
          this.maximumRoadMappingErrorMeters,
          lanePose.roadMappingErrorMeters,
        )
      } else if (rejectedInsideDetailedLane) {
        this.offRoadVehicleCount += 1
      }
      if (rawFallback && this.lanePoseResolver?.hasLane(vehicle.lane_id)) {
        this.detailedAreaRawFallbackCount += 1
      }
      if (rejectedInsideDetailedLane) {
        this.invalidPoseSuppressedVehicles.add(vehicle.vehicle_id)
        this.invalidPoseReentrySnapshots.delete(vehicle.vehicle_id)
        this.temporarilyHiddenCount += 1
        this.temporarilyHiddenVehicleIds.add(vehicle.vehicle_id)
        return null
      }
      if (lanePose && this.invalidPoseSuppressedVehicles.has(vehicle.vehicle_id)) {
        const previous = this.invalidPoseReentrySnapshots.get(vehicle.vehicle_id)
        const count = previous?.sequence === context.sequence ? previous.count : (previous?.count ?? 0) + 1
        this.invalidPoseReentrySnapshots.set(vehicle.vehicle_id, { count, sequence: context.sequence })
        if (count < 2) {
          this.temporarilyHiddenCount += 1
          this.temporarilyHiddenVehicleIds.add(vehicle.vehicle_id)
          return null
        }
        this.invalidPoseSuppressedVehicles.delete(vehicle.vehicle_id)
        this.invalidPoseReentrySnapshots.delete(vehicle.vehicle_id)
      }
      const laneHeading = lanePose?.heading
        ?? (fallbackLaneHeading != null
          && sourceMapHeading != null
          && vehicle.speed > 0.35
          && Math.abs(shortestAngleDelta(fallbackLaneHeading, sourceMapHeading)) <= 35 * Math.PI / 180
          ? fallbackLaneHeading
          : null)
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
        telemetryReliable,
        sourceMapHeading,
        headingResolution,
        heldForLaneRecovery: false,
        laneChanging,
        rawFallback,
        rejectionReason: laneChanging
          ? 'lane_change_in_progress'
          : rawFallback ? 'no_compatible_topology_pose' : undefined,
        routeTurnResolution,
      }
    }).filter((draft): draft is NonNullable<typeof draft> => draft !== null)

    const generatedSamples = drafts.flatMap((draft) => {
      const { vehicle, profile, previousHeading, previousPose } = draft
      if (draft.heldForLaneRecovery && previousPose) {
        return this.retainStablePoseSample(
          vehicle,
          profile,
          previousPose,
          sourceTime,
          context,
          activeIds,
        )
      }
      const lanePose: ResolvedLanePose | null = draft.lanePose
      const renderLongitude = lanePose?.longitude ?? draft.longitude
      const renderLatitude = lanePose?.latitude ?? draft.latitude
      let point = { longitude: renderLongitude, latitude: renderLatitude }
      const frontToCenterOffsetMeters = lanePose?.modelCenterResolved === false
        ? profile.targetLengthMeters / 2
        : lanePose ? 0 : profile.targetLengthMeters / 2
      const centerHeading = draft.sourceMapHeading ?? lanePose?.heading ?? previousPose?.heading
      if (frontToCenterOffsetMeters > 0 && centerHeading != null) {
        point = moveFromFrontBumperToModelCenter(
          point,
          centerHeading,
          frontToCenterOffsetMeters,
        )
      }
      const laneCenterArcDistanceMeters = lanePose
        ? lanePose.arcDistanceMeters - frontToCenterOffsetMeters
        : undefined
      const pathCenterArcDistanceMeters = lanePose
        ? lanePose.pathArcDistanceMeters - frontToCenterOffsetMeters
        : undefined
      let activeConnectionLock = this.connectionLocksByVehicleId.get(vehicle.vehicle_id)
      const dynamicConnectionEvidence: DynamicConnectionEvidence = activeConnectionLock?.connectionKey
        ? {
            source: activeConnectionLock.source === 'fixed_route_index'
              ? 'fixed_route'
              : vehicle.lane_id.startsWith(':') ? 'live_via' : 'buffered_lookahead',
            connectionKey: activeConnectionLock.connectionKey,
            observedLaneId: vehicle.lane_id,
            fromLaneId: activeConnectionLock.fromLaneId,
            toLaneId: activeConnectionLock.toLaneId,
            viaLaneIds: activeConnectionLock.viaLaneIds,
          }
        : {
            source: 'unresolved',
            observedLaneId: vehicle.lane_id,
          }
      if (vehicle.vehicle_id.startsWith('event_vehicle_')) this.dynamicRuntimeVehicleCount += 1
      if (dynamicConnectionEvidence.source === 'buffered_lookahead') {
        this.bufferedLookaheadConnectionCount += 1
      }
      const topologyMotionBridge = Boolean(
        previousPose?.motionPathKey
        && lanePose?.motionPathKey
        && previousPose.motionPathKey !== lanePose.motionPathKey
        && activeConnectionLock?.connectionKey
        && (
          previousPose.connectionKey === activeConnectionLock.connectionKey
          || (
            previousPose.laneId === activeConnectionLock.fromLaneId
            && (
              activeConnectionLock.viaLaneIds?.includes(vehicle.lane_id)
              || vehicle.lane_id === activeConnectionLock.toLaneId
            )
          )
        ),
      )
      if (
        activeConnectionLock?.stage === 'exiting'
        && lanePose
        && lanePose.naturalFrontDistanceMeters >= profile.targetLengthMeters - 0.05
      ) {
        this.connectionLocksByVehicleId.delete(vehicle.vehicle_id)
        activeConnectionLock = undefined
      }
      const motionEpoch = previousPose?.motionEpoch ?? 0
      const candidateMotionPathKey = lanePose?.motionPathKey
        ?? (draft.rawFallback || draft.laneChanging
          ? `raw:${vehicle.road_id}:${vehicle.lane_id}`
          : previousPose?.motionPathKey)
      const candidateTransitionKind = lanePose?.transitionKind
        ?? (draft.rawFallback || draft.laneChanging
          ? 'raw_fallback'
          : previousPose?.transitionKind)
      const displacementStable = !previousPose || vehiclePoseDisplacementIsStable(
        previousPose,
        point,
        vehicle.speed,
        context.elapsedSeconds,
      )
      const headingDeltaRadians = previousPose && draft.sourceMapHeading != null
        ? Math.abs(shortestAngleDelta(previousPose.heading, draft.sourceMapHeading))
        : undefined
      const roadTransitionKind = classifyRoadTransition({
        previous: previousPose,
        roadId: vehicle.road_id,
        laneId: vehicle.lane_id,
        motionPathKey: candidateMotionPathKey,
        laneTransitionKind: candidateTransitionKind,
        laneChanging: draft.laneChanging,
        rawFallback: draft.rawFallback,
        displacementStable,
        headingDeltaRadians,
      })
      if (
        previousPose
        && !displacementStable
      ) {
        const pending = this.pendingPoseCandidates.get(vehicle.vehicle_id)
        const pendingMatches = pending
          && !pending.recovery
          && pending.laneId === vehicle.lane_id
          && pending.motionPathKey === lanePose?.motionPathKey
          && pending.segmentKey === lanePose?.segmentKey
          && pending.sequence !== context.sequence
          && vehiclePoseDisplacementMeters(pending.point, point) <= 6
        const stableSamples = pendingMatches ? pending.stableSamples + 1 : 1
        this.pendingPoseCandidates.set(vehicle.vehicle_id, {
          point,
          laneId: vehicle.lane_id,
          occupancyKey: lanePose?.occupancyKey,
          motionPathKey: lanePose?.motionPathKey,
          segmentKey: lanePose?.segmentKey,
          stableSamples,
          recovery: false,
          sequence: context.sequence,
        })
        if (stableSamples < 2) {
          activeIds.add(vehicle.vehicle_id)
          this.historyLastSeenSequence.set(vehicle.vehicle_id, context.sequence)
          return [{
            ...createVehicleTwinSample(
              vehicle,
              previousPose.longitude,
              previousPose.latitude,
              sourceTime,
              profile,
              previousPose.heading,
              true,
              {
                motionPathKey: previousPose.motionPathKey,
                connectionKey: previousPose.connectionKey,
                routeHintSource: previousPose.routeHintSource,
                segmentKey: previousPose.segmentKey,
                occupancyKey: previousPose.occupancyKey,
                arcDistanceMeters: previousPose.arcDistanceMeters,
                pathArcDistanceMeters: previousPose.pathArcDistanceMeters,
                transitionKind: previousPose.transitionKind,
                roadTransitionKind: previousPose.roadTransitionKind,
                poseSource: 'held',
                sampleQuality: 'held',
              },
            ),
            sceneGeneration: this.sceneGeneration,
            motionEpoch,
          }]
        }
        if (roadTransitionKind === 'incompatible') {
          return this.retainStablePoseSample(
            vehicle,
            profile,
            previousPose,
            sourceTime,
            context,
            activeIds,
            false,
            'incompatible_road_transition',
          )
        }
        this.pendingPoseCandidates.delete(vehicle.vehicle_id)
      } else {
        this.pendingPoseCandidates.delete(vehicle.vehicle_id)
      }
      if (draft.sourceMapHeading == null && !lanePose) {
        return previousPose
          ? this.retainStablePoseSample(vehicle, profile, previousPose, sourceTime, context, activeIds)
          : []
      }
      const resolved = resolveStableVehicleHeading({
        sourceMapHeading: draft.sourceMapHeading,
        speedMetersPerSecond: vehicle.speed,
        current: point,
        timeSeconds: context.elapsedSeconds,
        laneHeading: lanePose?.heading
          ?? draft.laneHeading,
        topologyConfirmed: Boolean(lanePose && !draft.laneChanging),
      }, previousHeading ?? null)
      const heading = resolved.heading
      this.poseHistory.set(vehicle.vehicle_id, resolved.state)
      this.historyLastSeenSequence.set(vehicle.vehicle_id, context.sequence)
      this.poseStates.set(vehicle.vehicle_id, {
        telemetryReliable: draft.telemetryReliable,
        backendDistance: draft.telemetryReliable ? vehicle.distance : previousPose?.backendDistance,
        routeId: draft.telemetryReliable ? vehicle.route_id : previousPose?.routeId,
        routeIndex: draft.telemetryReliable ? vehicle.route_index : previousPose?.routeIndex,
        roadId: vehicle.road_id,
        laneIndex: vehicle.lane_index,
        laneId: vehicle.lane_id,
        trackKey: lanePose?.trackKey ?? previousPose?.trackKey,
        motionPathKey: lanePose?.motionPathKey ?? (draft.rawFallback || draft.laneChanging
          ? `raw:${vehicle.road_id}:${vehicle.lane_id}`
          : previousPose?.motionPathKey),
        connectionKey: activeConnectionLock?.connectionKey,
        routeHintSource: activeConnectionLock?.source,
        segmentKey: lanePose?.segmentKey ?? (draft.rawFallback || draft.laneChanging
          ? `raw:${vehicle.lane_id}`
          : previousPose?.segmentKey),
        occupancyKey: lanePose?.occupancyKey ?? previousPose?.occupancyKey,
        trackProgress: lanePose?.trackProgress ?? previousPose?.trackProgress,
        trackDistanceMeters: laneCenterArcDistanceMeters ?? previousPose?.trackDistanceMeters,
        arcDistanceMeters: laneCenterArcDistanceMeters ?? previousPose?.arcDistanceMeters,
        pathArcDistanceMeters: pathCenterArcDistanceMeters
          ?? previousPose?.pathArcDistanceMeters,
        matchConfidence: lanePose?.matchConfidence ?? (draft.rawFallback ? 0 : previousPose?.matchConfidence),
        transitionKind: lanePose?.transitionKind ?? (draft.rawFallback || draft.laneChanging
          ? 'raw_fallback'
          : previousPose?.transitionKind),
        roadTransitionKind,
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
        lifecycle: draft.laneChanging
          ? 'laneChanging'
          : draft.rawFallback ? 'rawFallback' : 'stable',
        lastStableElapsedSeconds: draft.heldForLaneRecovery
          ? previousPose?.lastStableElapsedSeconds ?? context.elapsedSeconds
          : context.elapsedSeconds,
        authoritativeSourceLongitude: draft.sourceLongitude,
        authoritativeSourceLatitude: draft.sourceLatitude,
        sourceArcDistanceMeters: lanePose?.sourceArcDistanceMeters == null
          ? previousPose?.sourceArcDistanceMeters
          : lanePose.sourceArcDistanceMeters - frontToCenterOffsetMeters,
        sourceLateralOffsetMeters: lanePose?.sourceLateralOffsetMeters
          ?? previousPose?.sourceLateralOffsetMeters,
        longitude: point.longitude,
        latitude: point.latitude,
        heading,
        elapsedSeconds: context.elapsedSeconds,
        motionEpoch,
        lastSeenSequence: context.sequence,
      })
      const sumoHeading = draft.sourceMapHeading
      const laneHeading = lanePose?.heading ?? draft.laneHeading
      const sumoLaneDeltaDegrees = laneHeading == null || sumoHeading == null
        ? null
        : Math.abs(shortestAngleDelta(sumoHeading, laneHeading)) * 180 / Math.PI
      const headingReference = draft.laneChanging || draft.rawFallback
        ? sumoHeading
        : laneHeading
      const headingErrorDegrees = headingReference == null
        ? null
        : Math.abs(shortestAngleDelta(heading, headingReference)) * 180 / Math.PI
      this.vehiclePoseDiagnostics.push({
        vehicleId: vehicle.vehicle_id,
        laneId: vehicle.lane_id,
        traciAngleDegrees: vehicle.angle,
        sumoHeadingDegrees: sumoHeading == null ? null : sumoHeading * 180 / Math.PI,
        headingTransformAnchorIds: draft.headingResolution?.anchorIds ?? [],
        headingTransformLocal: draft.headingResolution?.local ?? null,
        laneHeadingDegrees: laneHeading == null ? null : laneHeading * 180 / Math.PI,
        twinDirectionDegrees: (heading - profile.modelForwardAxisAngle) * 180 / Math.PI,
        finalModelHeadingDegrees: heading * 180 / Math.PI,
        headingErrorDegrees,
        sumoLaneDeltaDegrees,
        motionPathKey: lanePose?.motionPathKey,
        connectionKey: activeConnectionLock?.connectionKey,
        routeHintStatus: draft.routeTurnResolution.status,
        routeHintSource: activeConnectionLock?.source,
        connectionLockStage: activeConnectionLock?.stage,
        segmentKey: lanePose?.segmentKey,
        lifecycle: draft.laneChanging
          ? 'laneChanging'
          : draft.rawFallback ? 'rawFallback' : 'stable',
        transitionKind: lanePose?.transitionKind ?? (draft.rawFallback ? 'raw_fallback' : undefined),
        roadTransitionKind,
        rejectionReason: draft.rejectionReason,
        sourceLongitude: draft.sourceLongitude,
        sourceLatitude: draft.sourceLatitude,
        mappedLongitude: point.longitude,
        mappedLatitude: point.latitude,
        sourceArcDistanceMeters: lanePose?.sourceArcDistanceMeters,
        sourceLateralOffsetMeters: lanePose?.sourceLateralOffsetMeters,
        roadMappingErrorMeters: lanePose?.roadMappingErrorMeters,
        mappingMode: lanePose?.mappingMode,
        coverageKind: this.lanePoseResolver?.hasLane(vehicle.lane_id)
          ? 'local_manifest'
          : this.lanePoseResolver?.coversDetailedArea([point.longitude, point.latitude])
            ? 'supplemental_network'
            : 'outside',
      })
      activeIds.add(vehicle.vehicle_id)
      return [{
        ...createVehicleTwinSample(
          vehicle,
          point.longitude,
          point.latitude,
          sourceTime,
          profile,
          heading,
          true,
          {
            motionPathKey: lanePose?.motionPathKey ?? `raw:${vehicle.road_id}:${vehicle.lane_id}`,
            connectionKey: activeConnectionLock?.connectionKey,
            routeHintSource: activeConnectionLock?.source,
            connectionLockStage: activeConnectionLock?.stage,
            dynamicConnectionEvidence,
            laneChangeCorridorKey: draft.laneChanging && previousPose?.motionPathKey
              ? `${previousPose.motionPathKey}->${lanePose?.motionPathKey ?? candidateMotionPathKey}`
              : undefined,
            motionPathBridgeKey: !draft.laneChanging
              && (previousPose?.laneId === vehicle.lane_id || topologyMotionBridge)
              && previousPose?.motionPathKey
              && lanePose?.motionPathKey
              && previousPose.motionPathKey !== lanePose.motionPathKey
              ? `${previousPose.motionPathKey}->${lanePose.motionPathKey}`
              : undefined,
            corridorMotionPathKeys: draft.laneChanging
              ? [...new Set([
                  previousPose?.motionPathKey,
                  lanePose?.motionPathKey,
                ].filter((key): key is string => Boolean(key)))]
              : [...new Set([
                  previousPose?.laneId === vehicle.lane_id || topologyMotionBridge
                    ? previousPose?.motionPathKey
                    : undefined,
                  lanePose?.motionPathKey,
                ].filter((key): key is string => Boolean(key)))],
            detailedCorridorValidation: Boolean(
              lanePose
              && this.lanePoseResolver?.coversDetailedArea([point.longitude, point.latitude])
            ),
            rawTransitionValidated: roadTransitionKind === 'raw_continuous'
              && displacementStable
              && !this.lanePoseResolver?.hasLane(vehicle.lane_id),
            segmentKey: lanePose?.segmentKey ?? `raw:${vehicle.lane_id}`,
            occupancyKey: lanePose?.occupancyKey ?? previousPose?.occupancyKey,
            arcDistanceMeters: laneCenterArcDistanceMeters,
            pathArcDistanceMeters: pathCenterArcDistanceMeters,
            predictionMaximumPathArcDistanceMeters: lanePose
              && pathCenterArcDistanceMeters != null
              && laneCenterArcDistanceMeters != null
              && lanePose.stopFrontLimitDistanceMeters != null
              && laneRuntimeRequiresStop(context.laneRuntimeById?.[vehicle.lane_id])
              ? pathCenterArcDistanceMeters + Math.max(
                  0,
                  lanePose.stopFrontLimitDistanceMeters
                    - profile.targetLengthMeters / 2
                    - laneCenterArcDistanceMeters,
                )
              : undefined,
            transitionKind: lanePose?.transitionKind ?? 'raw_fallback',
            roadTransitionKind,
            sourceArcDistanceMeters: lanePose?.sourceArcDistanceMeters == null
              ? previousPose?.sourceArcDistanceMeters
              : lanePose.sourceArcDistanceMeters - frontToCenterOffsetMeters,
            sourceLateralOffsetMeters: lanePose?.sourceLateralOffsetMeters
              ?? previousPose?.sourceLateralOffsetMeters,
            poseSource: draft.laneChanging
              ? 'lane_change'
              : lanePose ? 'topology' : 'raw',
          },
        ),
        sceneGeneration: this.sceneGeneration,
        motionEpoch,
      }]
    })
    const uniqueSamples = new Map<string, VehicleTwinSample>()
    for (const sample of generatedSamples) {
      if (uniqueSamples.has(sample.id)) this.duplicateVehicleIds.add(sample.id)
      uniqueSamples.set(sample.id, sample)
    }
    const samples = [...uniqueSamples.values()]
    this.visibleCount = samples.length
    this.pruneHistory(context.sequence, activeIds)
    this.motionBuffer.push({
      sceneGeneration: this.sceneGeneration,
      sequence: context.sequence,
      elapsedSeconds: context.elapsedSeconds,
      arrivalTimeMs: performance.now(),
      samples,
      presentVehicleIds: renderableVehicles.map((vehicle) => vehicle.vehicle_id),
    })
    this.recordSourceRoster(context.sequence, samples.length)
    if (!terminalState && !isVehicleAnimationActive(context.state) && samples.length > 0) {
      this.presentImmediate(samples)
    }
    return this.stats(vehicles.length, radiusMeters)
  }

  refreshViewport(): VehicleRenderStats {
    return this.update(this.lastVehicles, this.lastContext, true)
  }

  beginViewportTransition(): void {
    // Keep the active Twin and compiled samples alive while the new resolver is
    // prepared. Incoming authoritative snapshots are staged in the ring buffer.
    this.sceneGeneration += 1
    this.viewportTransitionActive = true
  }

  setActive(active: boolean): void {
    if (this.active === active) return
    this.active = active
    if (active) {
      const startableTwin = this.twin as mapvthree.Twin & { start?: () => void }
      startableTwin.start?.()
      this.outputPacer.reset()
      this.twinPlaybackBacklogMs = 0
      this.update(this.lastVehicles, this.lastContext, true)
      const samples = this.motionBuffer.sample(
        performance.now(),
        this.sharedDisplayElapsedSeconds,
      )
      if (samples?.length) this.presentImmediate(samples)
    } else {
      if (this.outputFrameId !== null) cancelAnimationFrame(this.outputFrameId)
      this.outputFrameId = null
      this.motionBuffer.pause()
      this.outputPacer.reset()
      this.twinPlaybackBacklogMs = 0
      const pausableTwin = this.twin as mapvthree.Twin & { pause?: () => void }
      pausableTwin.pause?.()
    }
    this.syncMotionFrameScheduling()
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
    this.emptySourceSnapshotStreak = 0
    this.lastRosterSequence = -1
    this.outputPacer.reset()
    this.twinPlaybackBacklogMs = 0
    this.lastTwinPushWallTimeMs = null
    this.maximumTwinOutputGapMs = 0
    this.twinGapFillFrameCount = 0
    this.emptyBufferInterceptCount = 0
    this.motionBuffer.reset()
    this.presentationClock.reset()
    this.selector.reset()
    this.renderBudget.reset()
    this.poseHistory.clear()
    this.poseStates.clear()
    this.twinDirectionByVehicleId.clear()
    this.historyLastSeenSequence.clear()
    this.pendingPoseCandidates.clear()
    this.pendingLaneChanges.clear()
    this.routeCursorByVehicleId.clear()
    this.connectionLocksByVehicleId.clear()
    this.invalidPoseSuppressedVehicles.clear()
    this.invalidPoseReentrySnapshots.clear()
    this.viewportSnapshots.splice(0)
    this.replayingViewportSnapshots = false
    this.viewportPrecompileMilliseconds = 0
    this.viewportTwinBlankFrameCount = 0
    this.viewportFirstFrameVehicleCount = 0
    this.surfaceExclusionVehicleFilterCount = 0
    this.viewportTransitionActive = false
    this.terminalFreezeActive = false
    this.twin.reset()
    this.twinResetCount += 1
    if (this.active) {
      const startableTwin = this.twin as mapvthree.Twin & { start?: () => void }
      startableTwin.start?.()
    }
    this.syncMotionFrameScheduling()
  }

  private scheduleMotionFrame(): void {
    if (
      !this.active
      || !isVehicleAnimationActive(this.lastContext.state)
      || this.outputFrameId !== null
    ) return
    this.outputFrameId = requestAnimationFrame((wallTimeMs) => {
      this.outputFrameId = null
      this.flushMotionFrame(wallTimeMs)
      this.scheduleMotionFrame()
    })
  }

  private syncMotionFrameScheduling(): void {
    const shouldRun = this.active
      && isVehicleAnimationActive(this.lastContext.state)
    if (!shouldRun && this.outputFrameId !== null) {
      cancelAnimationFrame(this.outputFrameId)
      this.outputFrameId = null
    }
    if (shouldRun) this.scheduleMotionFrame()
  }

  private flushMotionFrame(wallTimeMs: number): void {
    if (!isVehicleAnimationActive(this.lastContext.state)) return
    const budget = this.renderBudget.recordFrame(wallTimeMs)
    const frameInterval = this.stableOutputMode || budget.quality === 'constrained'
      ? CONSTRAINED_OUTPUT_FRAME_MS
      : NORMAL_OUTPUT_FRAME_MS
    const pacing = this.outputPacer.next(wallTimeMs, frameInterval)
    if (!pacing) return
    if (pacing.catchingUp) this.twinGapFillFrameCount += 1
    this.twinPlaybackBacklogMs = pacing.backlogMs
    const pushed = this.pushMotionFrameAt(pacing.sampleWallTimeMs, wallTimeMs)
    if (pushed) this.engine.requestRender()
  }

  private pushMotionFrameAt(sampleWallTimeMs: number, currentWallTimeMs: number): boolean {
    const samples = this.motionBuffer.sample(
      sampleWallTimeMs,
      this.sharedDisplayElapsedSeconds,
    )
    if (samples === null) {
      this.emptyBufferInterceptCount += 1
      if (this.viewportTransitionActive) this.viewportTwinBlankFrameCount += 1
      return false
    }
    if (samples.length === 0) {
      this.emptyBufferInterceptCount += 1
      if (this.primed) {
        this.twin.reset()
        this.twinResetCount += 1
        this.primed = false
        this.confirmedRemovedCount += 1
        this.engine.requestRender()
      }
      return false
    }
    const time = this.presentationClock.next(Date.now())
    this.recordTwinPushGap(currentWallTimeMs)
    const timedSamples = this.prepareTwinSamples(samples, time)
    const displayElapsedSeconds = timedSamples.find((sample) => (
      Number.isFinite(sample.displayElapsedSeconds)
    ))?.displayElapsedSeconds
    if (displayElapsedSeconds != null) this.onDisplayElapsedSeconds?.(displayElapsedSeconds)
    if (!this.primed) {
      this.twin.push(timedSamples.map((sample) => ({
        ...sample,
        time: time - TWIN_INITIAL_SAMPLE_SPACING_MS,
      })))
      this.primed = true
    }
    this.twin.push(timedSamples)
    return true
  }

  private recordSourceRoster(sequence: number, sampleCount: number): void {
    if (!Number.isFinite(sequence) || sequence === this.lastRosterSequence) return
    this.lastRosterSequence = sequence
    this.emptySourceSnapshotStreak = sampleCount === 0
      ? this.emptySourceSnapshotStreak + 1
      : 0
  }

  private retainStablePoseSample(
    vehicle: TrafficVehicleView,
    profile: VehicleModelProfile,
    previousPose: VehiclePoseState,
    sourceTime: number,
    context: VehicleRenderContext,
    activeIds: Set<string>,
    predictionBlocked = false,
    stopReason?: string,
  ): VehicleTwinSample[] {
    this.temporarilyHiddenCount += 1
    this.temporarilyHiddenVehicleIds.add(vehicle.vehicle_id)
    activeIds.add(vehicle.vehicle_id)
    this.historyLastSeenSequence.set(vehicle.vehicle_id, context.sequence)
    this.poseStates.set(vehicle.vehicle_id, {
      ...previousPose,
      lifecycle: 'recovering',
      laneResolutionFailures: previousPose.lastSeenSequence === context.sequence
        ? previousPose.laneResolutionFailures
        : previousPose.laneResolutionFailures + 1,
      elapsedSeconds: context.elapsedSeconds,
      lastSeenSequence: context.sequence,
    })
    void profile
    void sourceTime
    void predictionBlocked
    void stopReason
    return []
  }

  private presentImmediate(samples: VehicleTwinSample[]): void {
    const time = this.presentationClock.next(Date.now())
    const timedSamples = this.prepareTwinSamples(samples, time)
    if (!this.primed) {
      this.twin.reset()
      this.twinResetCount += 1
    }
    this.twin.push(timedSamples.map((sample) => ({
      ...sample,
      time: time - TWIN_INITIAL_SAMPLE_SPACING_MS,
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
      twinSafetyMarginMs: Math.max(0, TWIN_INTERPOLATION_DELAY_MS - this.maximumTwinOutputGapMs),
      maximumTwinOutputGapMs: this.maximumTwinOutputGapMs,
      emptyBufferInterceptCount: this.emptyBufferInterceptCount,
      terminalFreezeActive: this.terminalFreezeActive,
      laneRecoveryVehicleIds: [...this.laneRecoveryVehicleIds].slice(0, 50),
      temporarilyHiddenVehicleIds: [...this.temporarilyHiddenVehicleIds].slice(0, 50),
      duplicateVehicleIds: [...this.duplicateVehicleIds].slice(0, 50),
      incompatiblePathInterpolationCount: motion.incompatiblePathInterpolationCount,
      incompatiblePathInterpolationBlockedCount: motion.incompatiblePathInterpolationBlockedCount,
      poseViolationCount: this.vehiclePoseDiagnostics.filter((item) => (
        item.headingErrorDegrees != null
        && item.headingErrorDegrees > (item.lifecycle === 'laneChanging' ? 12 : 8)
      )).length,
      targetBufferSeconds: motion.targetBufferSeconds,
      expectedPlaybackRate: motion.expectedPlaybackRate,
      globalBufferDepthSeconds: motion.globalBufferDepthSeconds,
      globalPlaybackRate: motion.globalPlaybackRate,
      globalUnderrunPauseSeconds: motion.globalUnderrunPauseSeconds,
      authoritativeInterpolationCount: motion.authoritativeInterpolationCount,
      visibleTeleportCount: motion.visibleTeleportCount,
      pathResetCount: motion.pathResetCount,
      movingFreezeFrameCount: motion.movingFreezeFrameCount,
      batchArrivalCount: motion.batchArrivalCount,
      twinGapFillFrameCount: this.twinGapFillFrameCount,
      twinPlaybackBacklogMs: this.twinPlaybackBacklogMs,
      vehicleScaleViolationCount: motion.vehicleScaleViolationCount,
      normalTransitionEpochViolationCount: motion.normalTransitionEpochViolationCount,
      offRoadVehicleCount: this.offRoadVehicleCount,
      stuckLaneChangeCount: this.stuckLaneChangeCount,
      maximumRoadMappingErrorMeters: this.maximumRoadMappingErrorMeters,
      routeHintHitCount: this.routeHintHitCount,
      routeHintMismatchCount: this.routeHintMismatchCount,
      ambiguousRouteCandidateRejectionCount: this.ambiguousRouteCandidateRejectionCount,
      ambiguousIncomingPendingCount: this.ambiguousIncomingPendingCount,
      staleConnectionReleaseCount: this.staleConnectionReleaseCount,
      connectionMismatchCount: this.connectionMismatchCount,
      laneChangeCorridorViolationCount: motion.laneChangeCorridorViolationCount,
      intermediateOffRoadFrameCount: motion.intermediateOffRoadFrameCount,
      detailedAreaRawFallbackCount: this.detailedAreaRawFallbackCount,
      compiledSegmentCount: motion.compiledSegmentCount,
      rejectedCompiledSegmentCount: motion.rejectedCompiledSegmentCount,
      endpointValidationFailureCount: motion.endpointValidationFailureCount,
      compiledReadyElapsedSeconds: motion.compiledReadyElapsedSeconds,
      dynamicRuntimeVehicleCount: this.dynamicRuntimeVehicleCount,
      bufferedLookaheadConnectionCount:
        this.bufferedLookaheadConnectionCount + motion.bufferedLookaheadSegmentCount,
      compiledSegmentCacheHitCount: motion.compiledSegmentCacheHitCount,
      compiledSegmentCacheHitRate: motion.compiledSegmentCacheHitRate,
      isolatedVehicleCount: motion.isolatedVehicleCount,
      maximumIsolationSeconds: motion.maximumIsolationSeconds,
      recoveredVehicleCount: motion.recoveredVehicleCount,
      ghostVehicleIds: motion.ghostVehicleIds,
      hiddenUnresolvedVehicleIds: motion.hiddenUnresolvedVehicleIds,
      viewportPrecompileMilliseconds: this.viewportPrecompileMilliseconds,
      viewportTwinBlankFrameCount: this.viewportTwinBlankFrameCount,
      viewportFirstFrameVehicleCount: this.viewportFirstFrameVehicleCount,
      surfaceExclusionVehicleFilterCount: this.surfaceExclusionVehicleFilterCount,
      vehiclePoseDiagnostics: this.vehiclePoseDiagnostics.slice(0, 100),
      displayElapsedSeconds: motion.renderElapsedSeconds,
    }
  }

  private pruneHistory(sequence: number, activeIds: Set<string> = new Set()): void {
    if (!Number.isFinite(sequence) || sequence < 0) return
    for (const [id, lastSeenSequence] of this.historyLastSeenSequence) {
      if (activeIds.has(id) || sequence - lastSeenSequence <= VEHICLE_HISTORY_TTL_SNAPSHOTS) continue
      this.historyLastSeenSequence.delete(id)
      this.poseHistory.delete(id)
      this.poseStates.delete(id)
      this.pendingPoseCandidates.delete(id)
      this.pendingLaneChanges.delete(id)
      this.twinDirectionByVehicleId.delete(id)
      this.routeCursorByVehicleId.delete(id)
      this.connectionLocksByVehicleId.delete(id)
      this.invalidPoseSuppressedVehicles.delete(id)
      this.invalidPoseReentrySnapshots.delete(id)
    }
  }

  private prepareTwinSamples(samples: VehicleTwinSample[], time: number): VehicleTwinSample[] {
    return samples.map((sample) => {
      const previous = this.twinDirectionByVehicleId.get(sample.id) ?? null
      const modelDirection = Number.isFinite(sample.dir) ? sample.dir : 0
      const unwrapped = unwrapVehicleModelDirection(
        previous,
        Number.isFinite(sample.vehicleHeading)
          ? sample.vehicleHeading
          : modelDirection + (Number(sample.modelForwardAxisAngle) || 0),
        Number(sample.modelForwardAxisAngle) || 0,
      )
      this.twinDirectionByVehicleId.set(sample.id, unwrapped)
      return {
        ...sample,
        dir: unwrapped,
        unwrappedModelDirection: unwrapped,
        time,
      }
    })
  }

  private recordViewportSnapshot(
    vehicles: TrafficVehicleView[],
    context: VehicleRenderContext,
  ): void {
    if (!Number.isFinite(context.sequence) || !Number.isFinite(context.elapsedSeconds)) return
    const snapshot = {
      vehicles: [...vehicles],
      context: {
        ...context,
        laneRuntimeById: { ...(context.laneRuntimeById ?? {}) },
      },
    }
    const existingIndex = this.viewportSnapshots.findIndex((entry) => (
      entry.context.sessionId === context.sessionId
      && entry.context.sequence === context.sequence
    ))
    if (existingIndex >= 0) this.viewportSnapshots[existingIndex] = snapshot
    else this.viewportSnapshots.push(snapshot)
    this.viewportSnapshots.sort((left, right) => (
      left.context.elapsedSeconds - right.context.elapsedSeconds
      || left.context.sequence - right.context.sequence
    ))
    const latestElapsedSeconds = this.viewportSnapshots.at(-1)?.context.elapsedSeconds
      ?? context.elapsedSeconds
    while (
      this.viewportSnapshots.length > 1
      && (
        this.viewportSnapshots.length > MAX_VIEWPORT_STAGING_SNAPSHOTS
        || latestElapsedSeconds - this.viewportSnapshots[0].context.elapsedSeconds
          > VIEWPORT_SNAPSHOT_HISTORY_SECONDS
      )
    ) this.viewportSnapshots.shift()
  }

  private selectViewportReplayVehicles(
    vehicles: TrafficVehicleView[],
    limit: number,
  ): VisibleVehicle[] {
    const resolver = this.lanePoseResolver
    if (!resolver || limit <= 0) return []
    const priorityIds = this.viewportReplayPriorityVehicleIds
    const local: VisibleVehicle[] = []
    for (const vehicle of vehicles) {
      if (vehicle.longitude == null || vehicle.latitude == null) continue
      const [longitude, latitude] = this.projector([vehicle.longitude, vehicle.latitude, 0])
      if (
        !priorityIds?.has(vehicle.vehicle_id)
        && !resolver.hasLane(vehicle.lane_id)
        && !resolver.coversDetailedArea([longitude, latitude])
      ) continue
      local.push({
        vehicle,
        longitude,
        latitude,
        distanceMeters: 0,
      })
    }
    local.sort((left, right) => (
      Number(priorityIds?.has(right.vehicle.vehicle_id))
      - Number(priorityIds?.has(left.vehicle.vehicle_id))
      || left.vehicle.vehicle_id.localeCompare(right.vehicle.vehicle_id)
    ))
    return local.slice(0, limit)
  }

  private recordTwinPushGap(wallTimeMs: number): void {
    if (this.lastTwinPushWallTimeMs != null && wallTimeMs > this.lastTwinPushWallTimeMs) {
      this.maximumTwinOutputGapMs = Math.max(
        this.maximumTwinOutputGapMs,
        wallTimeMs - this.lastTwinPushWallTimeMs,
      )
    }
    this.lastTwinPushWallTimeMs = wallTimeMs
  }

  destroy(): void {
    if (this.outputFrameId !== null) cancelAnimationFrame(this.outputFrameId)
    this.outputFrameId = null
    this.clear()
    this.engine.remove(this.twin)
  }
}
