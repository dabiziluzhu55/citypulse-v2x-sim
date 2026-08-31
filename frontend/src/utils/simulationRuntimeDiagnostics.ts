import type { SimulationSnapshot } from '../types/simulation'
import type { VehicleMotionSampleResult, VehicleMotionWaitingReason } from '../mapv/vehicleMotionBuffer'

const MAX_INTERVAL_SAMPLES = 60

export interface VehicleRuntimeDiagnosticUpdate {
  inputCount: number
  visibleCount: number
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
  compiledSegmentCacheHitCount: number
  compiledSegmentCacheHitRate: number
  isolatedVehicleCount: number
  maximumIsolationSeconds: number
  recoveredVehicleCount: number
  viewportPrecompileMilliseconds: number
  viewportTwinBlankFrameCount: number
  viewportFirstFrameVehicleCount: number
  surfaceExclusionVehicleFilterCount: number
  motionSampleStatus: VehicleMotionSampleResult['status']
  motionWaitingReason: VehicleMotionWaitingReason | null
  authoritativeVehicleCount: number
  sourceVehicleCount: number
  viewportVehicleCount: number
  selectedVehicleCount: number
  playableVehicleCount: number
  twinOutputVehicleCount: number
  twinActualVisibleVehicleCount: number
  twinActualVisibleVehicleIds: string[]
  twinVisibleDisplayElapsedSeconds: number | null
  twinSubmittedWindowDepthMs: number
  twinWindowExhaustionCount: number
  waitingTwinResetInterceptCount: number
  workerCompilationQueueDepth: number
  legalCompiledSegmentCount: number
  twinResetReason: string | null
  requestedIntersectionId?: string
  committedIntersectionId?: string | null
  viewportStageStatus?: string
  viewportStageRejectionReasons?: string[]
  firstSourceElapsedSeconds: number | null
  latestSourceElapsedSeconds: number | null
  sourceVehicleIntersectionCount: number
  visualAddedIntersectionCount: number
  collisionRejectedVehicleIds: string[]
}

interface RuntimeDiagnosticState {
  sessionId: string
  officialTime: string
  backendElapsedSeconds: number
  requestedPlaybackSpeed: number
  achievedPlaybackSpeed: number | null
  snapshotGapP50Ms: number
  snapshotGapP95Ms: number
  snapshotGapP99Ms: number
  snapshotDecodeMs: number
  coalescedSnapshotCount: number
  websocketConnected: boolean
  websocketDisconnectCount: number
  websocketReconnectCount: number
  renderFps: number | null
  longTaskCount: number
  inputVehicles: number
  visibleVehicles: number
  clippedVehicles: number
  laneRecoveryVehicles: number
  temporarilyHiddenVehicles: number
  retainedMissingVehicles: number
  confirmedRemovedVehicles: number
  motionBufferSeconds: number
  motionBufferUnderruns: number
  motionBufferUnderrunActive: boolean
  twinResetCount: number
  twinSafetyMarginMs: number
  maximumTwinOutputGapMs: number
  emptyBufferInterceptCount: number
  terminalFreezeActive: boolean
  incompatiblePathInterpolations: number
  incompatiblePathInterpolationBlocks: number
  poseViolations: number
  targetMotionBufferSeconds: number
  expectedVehiclePlaybackRate: number
  globalBufferDepthSeconds: number
  globalVehiclePlaybackRate: number
  globalUnderrunPauseSeconds: number
  authoritativeInterpolations: number
  visibleTeleports: number
  pathResets: number
  movingFreezeFrames: number
  batchSnapshotArrivals: number
  twinGapFillFrames: number
  offRoadVehicles: number
  stuckLaneChanges: number
  maximumRoadMappingErrorMeters: number
  routeHintHits: number
  routeHintMismatches: number
  ambiguousRouteCandidateRejections: number
  ambiguousIncomingPending: number
  staleConnectionReleases: number
  connectionMismatches: number
  laneChangeCorridorViolations: number
  intermediateOffRoadFrames: number
  detailedAreaRawFallbacks: number
  compiledVehicleSegments: number
  compiledSegmentCacheHits: number
  compiledSegmentCacheHitRate: number
  isolatedVehicles: number
  maximumVehicleIsolationSeconds: number
  recoveredVehicleTimelines: number
  viewportPrecompileMilliseconds: number
  viewportTwinBlankFrames: number
  viewportFirstFrameVehicles: number
  surfaceExclusionVehicleFilters: number
  motionSampleStatus: VehicleMotionSampleResult['status']
  motionWaitingReason: VehicleMotionWaitingReason | null
  authoritativeVehicles: number
  sourceVehicles: number
  viewportVehicles: number
  selectedVehicles: number
  playableVehicles: number
  twinOutputVehicles: number
  twinActualVisibleVehicles: number
  twinActualVisibleVehicleIds: string[]
  twinVisibleDisplayElapsedSeconds: number | null
  twinSubmittedWindowDepthMs: number
  twinWindowExhaustionCount: number
  waitingTwinResetIntercepts: number
  workerCompilationQueueDepth: number
  legalCompiledSegments: number
  twinResetReason: string | null
  requestedIntersectionId: string
  committedIntersectionId: string | null
  viewportStageStatus: string
  viewportStageRejectionReasons: string[]
  firstVehicleSourceElapsedSeconds: number | null
  latestVehicleSourceElapsedSeconds: number | null
  sourceVehicleIntersections: number
  visualAddedIntersections: number
  collisionRejectedVehicleIds: string[]
  capturedAt: string
}

const state: RuntimeDiagnosticState = {
  sessionId: '',
  officialTime: '',
  backendElapsedSeconds: 0,
  requestedPlaybackSpeed: 1,
  achievedPlaybackSpeed: null,
  snapshotGapP50Ms: 0,
  snapshotGapP95Ms: 0,
  snapshotGapP99Ms: 0,
  snapshotDecodeMs: 0,
  coalescedSnapshotCount: 0,
  websocketConnected: false,
  websocketDisconnectCount: 0,
  websocketReconnectCount: 0,
  renderFps: null,
  longTaskCount: 0,
  inputVehicles: 0,
  visibleVehicles: 0,
  clippedVehicles: 0,
  laneRecoveryVehicles: 0,
  temporarilyHiddenVehicles: 0,
  retainedMissingVehicles: 0,
  confirmedRemovedVehicles: 0,
  motionBufferSeconds: 0,
  motionBufferUnderruns: 0,
  motionBufferUnderrunActive: false,
  twinResetCount: 0,
  twinSafetyMarginMs: 0,
  maximumTwinOutputGapMs: 0,
  emptyBufferInterceptCount: 0,
  terminalFreezeActive: false,
  incompatiblePathInterpolations: 0,
  incompatiblePathInterpolationBlocks: 0,
  poseViolations: 0,
  targetMotionBufferSeconds: 2,
  expectedVehiclePlaybackRate: 1,
  globalBufferDepthSeconds: 0,
  globalVehiclePlaybackRate: 1,
  globalUnderrunPauseSeconds: 0,
  authoritativeInterpolations: 0,
  visibleTeleports: 0,
  pathResets: 0,
  movingFreezeFrames: 0,
  batchSnapshotArrivals: 0,
  twinGapFillFrames: 0,
  offRoadVehicles: 0,
  stuckLaneChanges: 0,
  maximumRoadMappingErrorMeters: 0,
  routeHintHits: 0,
  routeHintMismatches: 0,
  ambiguousRouteCandidateRejections: 0,
  ambiguousIncomingPending: 0,
  staleConnectionReleases: 0,
  connectionMismatches: 0,
  laneChangeCorridorViolations: 0,
  intermediateOffRoadFrames: 0,
  detailedAreaRawFallbacks: 0,
  compiledVehicleSegments: 0,
  compiledSegmentCacheHits: 0,
  compiledSegmentCacheHitRate: 0,
  isolatedVehicles: 0,
  maximumVehicleIsolationSeconds: 0,
  recoveredVehicleTimelines: 0,
  viewportPrecompileMilliseconds: 0,
  viewportTwinBlankFrames: 0,
  viewportFirstFrameVehicles: 0,
  surfaceExclusionVehicleFilters: 0,
  motionSampleStatus: 'waiting',
  motionWaitingReason: 'insufficient_frames',
  authoritativeVehicles: 0,
  sourceVehicles: 0,
  viewportVehicles: 0,
  selectedVehicles: 0,
  playableVehicles: 0,
  twinOutputVehicles: 0,
  twinActualVisibleVehicles: 0,
  twinActualVisibleVehicleIds: [],
  twinVisibleDisplayElapsedSeconds: null,
  twinSubmittedWindowDepthMs: 0,
  twinWindowExhaustionCount: 0,
  waitingTwinResetIntercepts: 0,
  workerCompilationQueueDepth: 0,
  legalCompiledSegments: 0,
  twinResetReason: null,
  requestedIntersectionId: '',
  committedIntersectionId: null,
  viewportStageStatus: 'idle',
  viewportStageRejectionReasons: [],
  firstVehicleSourceElapsedSeconds: null,
  latestVehicleSourceElapsedSeconds: null,
  sourceVehicleIntersections: 0,
  visualAddedIntersections: 0,
  collisionRejectedVehicleIds: [],
  capturedAt: '',
}

let snapshotIntervalsMs: number[] = []
let lastSnapshotArrivalMs: number | null = null
let connectionObserved = false
let previousConnection = false

function percentile(values: number[], ratio: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((left, right) => left - right)
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]
}

function publish(): void {
  state.capturedAt = new Date().toISOString()
  if (typeof window === 'undefined' || !import.meta.env?.DEV) return
  const diagnosticWindow = window as Window & {
    __CITYPULSE_VEHICLE_DIAGNOSTICS__?: RuntimeDiagnosticState
  }
  diagnosticWindow.__CITYPULSE_VEHICLE_DIAGNOSTICS__ = { ...state }
}

export function resetSimulationRuntimeDiagnostics(sessionId = ''): void {
  state.sessionId = sessionId
  state.officialTime = ''
  state.backendElapsedSeconds = 0
  state.requestedPlaybackSpeed = 1
  state.achievedPlaybackSpeed = null
  state.snapshotGapP50Ms = 0
  state.snapshotGapP95Ms = 0
  state.snapshotGapP99Ms = 0
  state.snapshotDecodeMs = 0
  state.coalescedSnapshotCount = 0
  state.renderFps = null
  state.longTaskCount = 0
  state.inputVehicles = 0
  state.visibleVehicles = 0
  state.clippedVehicles = 0
  state.laneRecoveryVehicles = 0
  state.temporarilyHiddenVehicles = 0
  state.retainedMissingVehicles = 0
  state.confirmedRemovedVehicles = 0
  state.motionBufferSeconds = 0
  state.motionBufferUnderruns = 0
  state.motionBufferUnderrunActive = false
  state.twinResetCount = 0
  state.twinSafetyMarginMs = 0
  state.maximumTwinOutputGapMs = 0
  state.emptyBufferInterceptCount = 0
  state.terminalFreezeActive = false
  state.incompatiblePathInterpolations = 0
  state.incompatiblePathInterpolationBlocks = 0
  state.poseViolations = 0
  state.targetMotionBufferSeconds = 2
  state.expectedVehiclePlaybackRate = 1
  state.globalBufferDepthSeconds = 0
  state.globalVehiclePlaybackRate = 1
  state.globalUnderrunPauseSeconds = 0
  state.authoritativeInterpolations = 0
  state.visibleTeleports = 0
  state.pathResets = 0
  state.movingFreezeFrames = 0
  state.batchSnapshotArrivals = 0
  state.twinGapFillFrames = 0
  state.offRoadVehicles = 0
  state.stuckLaneChanges = 0
  state.maximumRoadMappingErrorMeters = 0
  state.routeHintHits = 0
  state.routeHintMismatches = 0
  state.ambiguousRouteCandidateRejections = 0
  state.ambiguousIncomingPending = 0
  state.staleConnectionReleases = 0
  state.connectionMismatches = 0
  state.laneChangeCorridorViolations = 0
  state.intermediateOffRoadFrames = 0
  state.detailedAreaRawFallbacks = 0
  state.compiledVehicleSegments = 0
  state.compiledSegmentCacheHits = 0
  state.compiledSegmentCacheHitRate = 0
  state.isolatedVehicles = 0
  state.maximumVehicleIsolationSeconds = 0
  state.recoveredVehicleTimelines = 0
  state.viewportPrecompileMilliseconds = 0
  state.viewportTwinBlankFrames = 0
  state.viewportFirstFrameVehicles = 0
  state.surfaceExclusionVehicleFilters = 0
  state.motionSampleStatus = 'waiting'
  state.motionWaitingReason = 'insufficient_frames'
  state.authoritativeVehicles = 0
  state.sourceVehicles = 0
  state.viewportVehicles = 0
  state.selectedVehicles = 0
  state.playableVehicles = 0
  state.twinOutputVehicles = 0
  state.twinActualVisibleVehicles = 0
  state.twinActualVisibleVehicleIds = []
  state.twinVisibleDisplayElapsedSeconds = null
  state.twinSubmittedWindowDepthMs = 0
  state.twinWindowExhaustionCount = 0
  state.waitingTwinResetIntercepts = 0
  state.workerCompilationQueueDepth = 0
  state.legalCompiledSegments = 0
  state.twinResetReason = null
  state.requestedIntersectionId = ''
  state.committedIntersectionId = null
  state.viewportStageStatus = 'idle'
  state.viewportStageRejectionReasons = []
  state.firstVehicleSourceElapsedSeconds = null
  state.latestVehicleSourceElapsedSeconds = null
  state.sourceVehicleIntersections = 0
  state.visualAddedIntersections = 0
  state.collisionRejectedVehicleIds = []
  snapshotIntervalsMs = []
  lastSnapshotArrivalMs = null
  publish()
}

export function recordSimulationDiagnosticSnapshot(
  snapshot: SimulationSnapshot,
  achievedPlaybackSpeed: number | null,
  arrivalTimeMs = Date.now(),
): void {
  if (state.sessionId !== snapshot.session_id) resetSimulationRuntimeDiagnostics(snapshot.session_id)
  if (lastSnapshotArrivalMs != null && arrivalTimeMs > lastSnapshotArrivalMs) {
    snapshotIntervalsMs.push(arrivalTimeMs - lastSnapshotArrivalMs)
    snapshotIntervalsMs = snapshotIntervalsMs.slice(-MAX_INTERVAL_SAMPLES)
  }
  lastSnapshotArrivalMs = arrivalTimeMs
  state.officialTime = snapshot.official_time
  state.backendElapsedSeconds = snapshot.elapsed_seconds
  state.requestedPlaybackSpeed = snapshot.playback_speed ?? 1
  state.achievedPlaybackSpeed = achievedPlaybackSpeed
  state.snapshotGapP50Ms = percentile(snapshotIntervalsMs, 0.5)
  state.snapshotGapP95Ms = percentile(snapshotIntervalsMs, 0.95)
  state.snapshotGapP99Ms = percentile(snapshotIntervalsMs, 0.99)
  publish()
}

export function recordSimulationDiagnosticConnection(connected: boolean): void {
  if (connectionObserved && connected !== previousConnection) {
    if (connected) state.websocketReconnectCount += 1
    else state.websocketDisconnectCount += 1
  }
  connectionObserved = true
  previousConnection = connected
  state.websocketConnected = connected
  publish()
}

export function recordSnapshotDecodeDiagnostics(
  parseDurationMs: number,
  coalescedSnapshotCount: number,
): void {
  if (Number.isFinite(parseDurationMs) && parseDurationMs >= 0) {
    state.snapshotDecodeMs = parseDurationMs
  }
  if (Number.isFinite(coalescedSnapshotCount) && coalescedSnapshotCount > 0) {
    state.coalescedSnapshotCount += Math.floor(coalescedSnapshotCount)
  }
  publish()
}

export function recordVehicleRuntimeDiagnostics(update: VehicleRuntimeDiagnosticUpdate): void {
  state.renderFps = update.fps
  state.inputVehicles = update.inputCount
  state.visibleVehicles = update.visibleCount
  state.clippedVehicles = Math.max(0, update.inputCount - update.visibleCount)
  state.laneRecoveryVehicles = update.laneRecoveryCount
  state.temporarilyHiddenVehicles = update.temporarilyHiddenCount
  state.retainedMissingVehicles = update.retainedMissingCount
  state.confirmedRemovedVehicles = update.confirmedRemovedCount
  state.motionBufferSeconds = update.bufferSeconds
  state.motionBufferUnderruns = update.underrunCount
  state.motionBufferUnderrunActive = update.underrunActive
  state.twinResetCount = update.twinResetCount
  state.twinSafetyMarginMs = update.twinSafetyMarginMs
  state.maximumTwinOutputGapMs = update.maximumTwinOutputGapMs
  state.emptyBufferInterceptCount = update.emptyBufferInterceptCount
  state.terminalFreezeActive = update.terminalFreezeActive
  state.incompatiblePathInterpolations = update.incompatiblePathInterpolationCount
  state.incompatiblePathInterpolationBlocks = update.incompatiblePathInterpolationBlockedCount
  state.poseViolations = update.poseViolationCount
  state.targetMotionBufferSeconds = update.targetBufferSeconds
  state.expectedVehiclePlaybackRate = update.expectedPlaybackRate
  state.globalBufferDepthSeconds = update.globalBufferDepthSeconds
  state.globalVehiclePlaybackRate = update.globalPlaybackRate
  state.globalUnderrunPauseSeconds = update.globalUnderrunPauseSeconds
  state.authoritativeInterpolations = update.authoritativeInterpolationCount
  state.visibleTeleports = update.visibleTeleportCount
  state.pathResets = update.pathResetCount
  state.movingFreezeFrames = update.movingFreezeFrameCount
  state.batchSnapshotArrivals = update.batchArrivalCount
  state.twinGapFillFrames = update.twinGapFillFrameCount
  state.offRoadVehicles = update.offRoadVehicleCount
  state.stuckLaneChanges = update.stuckLaneChangeCount
  state.maximumRoadMappingErrorMeters = update.maximumRoadMappingErrorMeters
  state.routeHintHits = update.routeHintHitCount
  state.routeHintMismatches = update.routeHintMismatchCount
  state.ambiguousRouteCandidateRejections = update.ambiguousRouteCandidateRejectionCount
  state.ambiguousIncomingPending = update.ambiguousIncomingPendingCount
  state.staleConnectionReleases = update.staleConnectionReleaseCount
  state.connectionMismatches = update.connectionMismatchCount
  state.laneChangeCorridorViolations = update.laneChangeCorridorViolationCount
  state.intermediateOffRoadFrames = update.intermediateOffRoadFrameCount
  state.detailedAreaRawFallbacks = update.detailedAreaRawFallbackCount
  state.compiledVehicleSegments = update.compiledSegmentCount
  state.compiledSegmentCacheHits = update.compiledSegmentCacheHitCount
  state.compiledSegmentCacheHitRate = update.compiledSegmentCacheHitRate
  state.isolatedVehicles = update.isolatedVehicleCount
  state.maximumVehicleIsolationSeconds = update.maximumIsolationSeconds
  state.recoveredVehicleTimelines = update.recoveredVehicleCount
  state.viewportPrecompileMilliseconds = update.viewportPrecompileMilliseconds
  state.viewportTwinBlankFrames = update.viewportTwinBlankFrameCount
  state.viewportFirstFrameVehicles = update.viewportFirstFrameVehicleCount
  state.surfaceExclusionVehicleFilters = update.surfaceExclusionVehicleFilterCount
  state.motionSampleStatus = update.motionSampleStatus
  state.motionWaitingReason = update.motionWaitingReason
  state.authoritativeVehicles = update.authoritativeVehicleCount
  state.sourceVehicles = update.sourceVehicleCount
  state.viewportVehicles = update.viewportVehicleCount
  state.selectedVehicles = update.selectedVehicleCount
  state.playableVehicles = update.playableVehicleCount
  state.twinOutputVehicles = update.twinOutputVehicleCount
  state.twinActualVisibleVehicles = update.twinActualVisibleVehicleCount
  state.twinActualVisibleVehicleIds = [...update.twinActualVisibleVehicleIds]
  state.twinVisibleDisplayElapsedSeconds = update.twinVisibleDisplayElapsedSeconds
  state.twinSubmittedWindowDepthMs = update.twinSubmittedWindowDepthMs
  state.twinWindowExhaustionCount = update.twinWindowExhaustionCount
  state.waitingTwinResetIntercepts = update.waitingTwinResetInterceptCount
  state.workerCompilationQueueDepth = update.workerCompilationQueueDepth
  state.legalCompiledSegments = update.legalCompiledSegmentCount
  state.twinResetReason = update.twinResetReason
  if (update.requestedIntersectionId !== undefined) {
    state.requestedIntersectionId = update.requestedIntersectionId
  }
  if (update.committedIntersectionId !== undefined) {
    state.committedIntersectionId = update.committedIntersectionId
  }
  if (update.viewportStageStatus !== undefined) {
    state.viewportStageStatus = update.viewportStageStatus
  }
  if (update.viewportStageRejectionReasons !== undefined) {
    state.viewportStageRejectionReasons = [...update.viewportStageRejectionReasons]
  }
  state.firstVehicleSourceElapsedSeconds = update.firstSourceElapsedSeconds
  state.latestVehicleSourceElapsedSeconds = update.latestSourceElapsedSeconds
  state.sourceVehicleIntersections = update.sourceVehicleIntersectionCount
  state.visualAddedIntersections = update.visualAddedIntersectionCount
  state.collisionRejectedVehicleIds = [...update.collisionRejectedVehicleIds]
  publish()
}

export function recordSimulationLongTasks(count: number): void {
  if (Number.isFinite(count) && count > 0) state.longTaskCount += Math.floor(count)
  publish()
}

export function runtimeDiagnosticsSnapshot(): Readonly<RuntimeDiagnosticState> {
  return { ...state }
}
