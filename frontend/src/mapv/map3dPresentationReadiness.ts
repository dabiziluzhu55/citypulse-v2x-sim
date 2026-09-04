export const MAP3D_MODULE_LOAD_TIMEOUT_MS = 60_000
export const MAP3D_BUILDING_SOFT_TIMEOUT_MS = 15_000
export const MAP3D_PRESENTATION_HARD_TIMEOUT_MS = 90_000
export const MAP3D_STALL_WINDOW_MS = 30_000
export const BUILDING_STABLE_SAMPLE_COUNT = 3
export const BUILDING_STABLE_SAMPLE_INTERVAL_MS = 250
export const FINAL_RENDER_FRAME_COUNT = 2
export const BUILDING_MIN_READY_TILES = 32

export type Map3dRenderQuality = 'full' | 'reduced'
export type Map3dPresentationDecision = 'wait' | 'present' | 'stalled' | 'hard-timeout'

export interface TilesetReadinessSample {
  readyTiles: number
  pendingRequests: number
  processingTiles: number
  attemptedRequests: number
  totalTiles: number
  cameraRevision: number
  nowMs: number
}

export interface BuildingLoadTracker {
  cameraRevision: number
  readyTiles: number
  demandedTiles: number
  totalTiles: number
  coverage: number
  activeRequests: number
  usableSamples: number
  settledSamples: number
  lastProgressAtMs: number
  previousReadyTiles: number
  previousActiveRequests: number
}

export interface Map3dPresentationSignals {
  providerReady: boolean
  cameraReady: boolean
  overviewReady: boolean
  intersectionReady: boolean
  environmentReady: boolean
  buildingRequired: boolean
  buildingUsable: boolean
  buildingReadyTiles: number
  buildingCoverage: number
}

function nonNegative(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0
}

export function createBuildingLoadTracker(
  nowMs = 0,
  cameraRevision = 0,
): BuildingLoadTracker {
  return {
    cameraRevision,
    readyTiles: 0,
    demandedTiles: 0,
    totalTiles: 0,
    coverage: 0,
    activeRequests: 0,
    usableSamples: 0,
    settledSamples: 0,
    lastProgressAtMs: nowMs,
    previousReadyTiles: 0,
    previousActiveRequests: 0,
  }
}

export function minimumBuildingReadyTiles(totalTiles: number): number {
  const total = Math.floor(nonNegative(totalTiles))
  return total > 0
    ? Math.min(BUILDING_MIN_READY_TILES, total)
    : BUILDING_MIN_READY_TILES
}

export function advanceBuildingLoadTracker(
  previous: BuildingLoadTracker,
  sample: TilesetReadinessSample,
  _quality: Map3dRenderQuality,
): BuildingLoadTracker {
  const base = previous.cameraRevision === sample.cameraRevision
    ? previous
    : createBuildingLoadTracker(sample.nowMs, sample.cameraRevision)
  const readyTiles = Math.floor(nonNegative(sample.readyTiles))
  const pendingRequests = Math.floor(nonNegative(sample.pendingRequests))
  const processingTiles = Math.floor(nonNegative(sample.processingTiles))
  const attemptedRequests = Math.floor(nonNegative(sample.attemptedRequests))
  const activeRequests = pendingRequests + processingTiles
  const currentDemand = readyTiles + activeRequests + attemptedRequests
  const demandedTiles = Math.max(base.demandedTiles, currentDemand)
  const coverage = demandedTiles > 0 ? Math.min(1, readyTiles / demandedTiles) : 0
  const totalTiles = Math.max(base.totalTiles, Math.floor(nonNegative(sample.totalTiles)))
  const demandChanged = demandedTiles !== base.demandedTiles
  const progressChanged = demandChanged
    || readyTiles !== base.previousReadyTiles
    || activeRequests !== base.previousActiveRequests
  const minimumReady = minimumBuildingReadyTiles(totalTiles)
  const usableNow = readyTiles >= minimumReady
  const settledNow = readyTiles > 0 && activeRequests === 0

  return {
    cameraRevision: sample.cameraRevision,
    readyTiles,
    demandedTiles,
    totalTiles,
    coverage,
    activeRequests,
    usableSamples: demandChanged
      ? 0
      : usableNow ? base.usableSamples + 1 : 0,
    settledSamples: settledNow ? base.settledSamples + 1 : 0,
    lastProgressAtMs: progressChanged ? sample.nowMs : base.lastProgressAtMs,
    previousReadyTiles: readyTiles,
    previousActiveRequests: activeRequests,
  }
}

export function buildingPresentationUsable(tracker: BuildingLoadTracker): boolean {
  return tracker.usableSamples >= BUILDING_STABLE_SAMPLE_COUNT
}

export function buildingPresentationSettled(tracker: BuildingLoadTracker): boolean {
  return tracker.settledSamples >= BUILDING_STABLE_SAMPLE_COUNT
}

export function buildingLoadStalled(
  tracker: BuildingLoadTracker,
  nowMs: number,
): boolean {
  return tracker.readyTiles === 0
    && nowMs - tracker.lastProgressAtMs >= MAP3D_STALL_WINDOW_MS
}

export function corePresentationReady(signals: Map3dPresentationSignals): boolean {
  return signals.providerReady
    && signals.cameraReady
    && signals.overviewReady
    && signals.intersectionReady
    && signals.environmentReady
}

export function map3dPresentationReady(signals: Map3dPresentationSignals): boolean {
  return corePresentationReady(signals)
    && (!signals.buildingRequired || signals.buildingUsable)
}

export function resolveMap3dPresentationDecision(
  signals: Map3dPresentationSignals,
  tracker: BuildingLoadTracker,
  elapsedMs: number,
  nowMs: number,
): Map3dPresentationDecision {
  if (map3dPresentationReady(signals)) return 'present'
  if (
    elapsedMs >= MAP3D_BUILDING_SOFT_TIMEOUT_MS
    && corePresentationReady(signals)
    && (!signals.buildingRequired || tracker.readyTiles > 0)
  ) return 'present'
  if (
    elapsedMs >= MAP3D_PRESENTATION_HARD_TIMEOUT_MS
    && corePresentationReady(signals)
    && (!signals.buildingRequired || tracker.readyTiles > 0)
  ) return 'present'
  if (
    corePresentationReady(signals)
    && signals.buildingRequired
    && buildingLoadStalled(tracker, nowMs)
  ) return 'stalled'
  if (
    elapsedMs >= MAP3D_PRESENTATION_HARD_TIMEOUT_MS
    && !corePresentationReady(signals)
  ) return 'hard-timeout'
  return 'wait'
}

export function map3dLoadingStage(signals: Map3dPresentationSignals): string {
  if (!signals.providerReady) return '正在初始化百度底图'
  if (!signals.cameraReady) return '正在定位20路口总览视角'
  if (!signals.overviewReady) return '正在加载20路口道路总览'
  if (!signals.intersectionReady) return '正在加载当前高精度路口'
  if (!signals.environmentReady) return '正在加载路灯与路口设施'
  if (signals.buildingRequired && !signals.buildingUsable) {
    const coverage = Math.round(signals.buildingCoverage * 100)
    return `正在加载当前视野内的本地建筑 · 已准备 ${signals.buildingReadyTiles} · 覆盖 ${coverage}%`
  }
  return '正在完成三维场景渲染'
}
