import type { CongestionLevel } from '../types/intelligence'
import type {
  SimulationIntersectionRuntime,
  SimulationLaneRuntime,
} from '../types/simulation'
import type { EventLanePositionIndexEntry } from './eventLanePositionIndex'

export interface LaneTrafficMetrics {
  laneId: string
  edgeId: string
  intersectionId: string
  ownerIntersectionId: string
  vehicleCount: number
  haltingCount: number
  meanSpeed: number
  occupancyPct: number
}

export type LaneCongestionFallbackReason =
  | 'owner_runtime_missing'
  | 'duplicate_runtime_conflict'
  | 'lane_geometry_missing'

export interface ResolvedLaneCongestionState extends LaneTrafficMetrics {
  level: CongestionLevel
  instantaneousLevel: CongestionLevel
  fallbackReason: LaneCongestionFallbackReason | null
}

export interface LaneCongestionStateDiagnostics {
  runtimeLaneCount: number
  drivingLaneCount: number
  duplicateLaneCount: number
  duplicateConflictCount: number
  unmappedLaneCount: number
  nonFreeLaneCount: number
  stabilizedChangeCount: number
}

export interface LaneCongestionStateSnapshot {
  sessionId: string
  presentationGeneration: number
  sequence: number
  asOfSeconds: number
  lanes: Readonly<Record<string, ResolvedLaneCongestionState>>
  edgeIdsWithLaneMetrics: ReadonlySet<string>
  diagnostics: Readonly<LaneCongestionStateDiagnostics>
}

interface StableLevelState {
  level: CongestionLevel
  pending: CongestionLevel | null
  pendingCount: number
}

interface RuntimeCandidate {
  laneId: string
  intersectionId: string
  runtime: SimulationLaneRuntime
}

const LEVEL_RANK: Record<CongestionLevel, number> = {
  free: 0,
  slow: 1,
  congested: 2,
  severe: 3,
}

function finiteNonNegative(value: unknown): number {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0
}

export function normalizeLaneOccupancyPct(value: unknown): number {
  const occupancy = finiteNonNegative(value)
  return occupancy <= 1 ? occupancy * 100 : occupancy
}

export function classifyLaneCongestion(metrics: Pick<
  LaneTrafficMetrics,
  'vehicleCount' | 'haltingCount' | 'meanSpeed' | 'occupancyPct'
>): CongestionLevel {
  const vehicleCount = Math.trunc(finiteNonNegative(metrics.vehicleCount))
  const haltingCount = Math.trunc(finiteNonNegative(metrics.haltingCount))
  const meanSpeed = finiteNonNegative(metrics.meanSpeed)
  const occupancyPct = normalizeLaneOccupancyPct(metrics.occupancyPct)
  if (vehicleCount <= 2 || (haltingCount <= 1 && occupancyPct < 5)) return 'free'
  const haltRatio = haltingCount / Math.max(vehicleCount, 1)
  if (
    haltingCount >= 6
    && meanSpeed <= 1
    && (haltRatio >= 0.6 || occupancyPct >= 35)
  ) return 'severe'
  if (
    haltingCount >= 4
    && meanSpeed <= 3
    && (haltRatio >= 0.4 || occupancyPct >= 20)
  ) return 'congested'
  if (haltingCount >= 2 && (meanSpeed <= 8 || occupancyPct >= 10)) return 'slow'
  return 'free'
}

function metricsForCandidate(
  candidate: RuntimeCandidate,
  ownerIntersectionId: string,
): LaneTrafficMetrics {
  const runtime = candidate.runtime
  return {
    laneId: candidate.laneId,
    edgeId: String(runtime.edge_id ?? candidate.laneId.replace(/_[^_]+$/, '')),
    intersectionId: candidate.intersectionId,
    ownerIntersectionId,
    vehicleCount: Math.trunc(finiteNonNegative(runtime.vehicle_count)),
    haltingCount: Math.trunc(finiteNonNegative(runtime.halting_count)),
    meanSpeed: finiteNonNegative(runtime.mean_speed),
    occupancyPct: normalizeLaneOccupancyPct(runtime.occupancy),
  }
}

function metricsDiffer(left: LaneTrafficMetrics, right: LaneTrafficMetrics): boolean {
  return left.edgeId !== right.edgeId
    || left.vehicleCount !== right.vehicleCount
    || left.haltingCount !== right.haltingCount
    || Math.abs(left.meanSpeed - right.meanSpeed) > 1e-6
    || Math.abs(left.occupancyPct - right.occupancyPct) > 1e-6
}

export class LaneCongestionStateResolver {
  private sessionKey = ''
  private levels = new Map<string, StableLevelState>()
  private lastResolutionKey = ''
  private lastSnapshot: LaneCongestionStateSnapshot | null = null
  private ownershipEntries: readonly EventLanePositionIndexEntry[] | null = null
  private ownerByLaneId = new Map<string, EventLanePositionIndexEntry>()
  private stabilizedChangeCount = 0

  resolve(input: {
    sessionId: string
    presentationGeneration: number
    sequence: number
    asOfSeconds: number
    intersections: Readonly<Record<string, SimulationIntersectionRuntime>>
    laneEntries?: readonly EventLanePositionIndexEntry[] | null
  }): LaneCongestionStateSnapshot {
    const sessionKey = `${input.sessionId}:${input.presentationGeneration}`
    if (sessionKey !== this.sessionKey) this.reset(sessionKey)
    this.updateOwnership(input.laneEntries ?? null)
    const resolutionKey = `${sessionKey}:${input.sequence}`
    if (resolutionKey === this.lastResolutionKey && this.lastSnapshot) return this.lastSnapshot

    const candidatesByLaneId = new Map<string, RuntimeCandidate[]>()
    for (const [intersectionId, intersection] of Object.entries(input.intersections)) {
      for (const [laneId, runtime] of Object.entries(intersection.lanes ?? {})) {
        const candidates = candidatesByLaneId.get(laneId) ?? []
        candidates.push({ laneId, intersectionId, runtime })
        candidatesByLaneId.set(laneId, candidates)
      }
    }

    let duplicateLaneCount = 0
    let duplicateConflictCount = 0
    let unmappedLaneCount = 0
    let nonFreeLaneCount = 0
    const lanes: Record<string, ResolvedLaneCongestionState> = {}
    const edgeIdsWithLaneMetrics = new Set<string>()
    const activeLaneIds = new Set<string>()

    for (const [laneId, candidates] of candidatesByLaneId) {
      const indexed = this.ownerByLaneId.get(laneId)
      if (indexed && indexed.kind !== 'driving') continue
      if (!indexed) unmappedLaneCount += 1
      if (candidates.length > 1) duplicateLaneCount += 1
      const ownerIntersectionId = indexed?.intersectionId ?? ''
      const ownerCandidate = candidates.find((candidate) => (
        candidate.intersectionId === ownerIntersectionId
      ))
      const candidateMetrics = candidates.map((candidate) => (
        metricsForCandidate(candidate, ownerIntersectionId)
      ))
      if (
        candidateMetrics.length > 1
        && candidateMetrics.some((metrics) => metricsDiffer(candidateMetrics[0], metrics))
      ) duplicateConflictCount += 1

      let selected = ownerCandidate
        ? metricsForCandidate(ownerCandidate, ownerIntersectionId)
        : candidateMetrics[0]
      let fallbackReason: LaneCongestionFallbackReason | null = indexed
        ? ownerCandidate ? null : 'owner_runtime_missing'
        : 'lane_geometry_missing'
      if (!ownerCandidate && candidateMetrics.length > 1) {
        selected = [...candidateMetrics].sort((left, right) => (
          LEVEL_RANK[classifyLaneCongestion(right)] - LEVEL_RANK[classifyLaneCongestion(left)]
          || right.vehicleCount - left.vehicleCount
        ))[0]
        fallbackReason = 'duplicate_runtime_conflict'
      }
      if (!selected.edgeId || selected.edgeId.startsWith(':')) continue

      const instantaneousLevel = classifyLaneCongestion(selected)
      const level = this.stabilize(laneId, instantaneousLevel)
      activeLaneIds.add(laneId)
      edgeIdsWithLaneMetrics.add(selected.edgeId)
      if (level !== 'free') nonFreeLaneCount += 1
      lanes[laneId] = {
        ...selected,
        level,
        instantaneousLevel,
        fallbackReason,
      }
    }
    for (const laneId of this.levels.keys()) {
      if (!activeLaneIds.has(laneId)) this.levels.delete(laneId)
    }

    const snapshot: LaneCongestionStateSnapshot = Object.freeze({
      sessionId: input.sessionId,
      presentationGeneration: input.presentationGeneration,
      sequence: input.sequence,
      asOfSeconds: input.asOfSeconds,
      lanes: Object.freeze(lanes),
      edgeIdsWithLaneMetrics,
      diagnostics: Object.freeze({
        runtimeLaneCount: candidatesByLaneId.size,
        drivingLaneCount: Object.keys(lanes).length,
        duplicateLaneCount,
        duplicateConflictCount,
        unmappedLaneCount,
        nonFreeLaneCount,
        stabilizedChangeCount: this.stabilizedChangeCount,
      }),
    })
    this.lastResolutionKey = resolutionKey
    this.lastSnapshot = snapshot
    return snapshot
  }

  reset(sessionKey = ''): void {
    this.sessionKey = sessionKey
    this.levels.clear()
    this.lastResolutionKey = ''
    this.lastSnapshot = null
    this.stabilizedChangeCount = 0
  }

  private updateOwnership(entries: readonly EventLanePositionIndexEntry[] | null): void {
    if (entries === this.ownershipEntries) return
    this.ownershipEntries = entries
    this.ownerByLaneId = new Map(entries?.map((entry) => [entry.laneId, entry] as const) ?? [])
    this.lastResolutionKey = ''
  }

  private stabilize(laneId: string, instantaneousLevel: CongestionLevel): CongestionLevel {
    const state = this.levels.get(laneId)
    if (!state) {
      this.levels.set(laneId, { level: instantaneousLevel, pending: null, pendingCount: 0 })
      return instantaneousLevel
    }
    if (state.level === instantaneousLevel) {
      state.pending = null
      state.pendingCount = 0
      return state.level
    }
    if (state.pending === instantaneousLevel) state.pendingCount += 1
    else {
      state.pending = instantaneousLevel
      state.pendingCount = 1
    }
    if (state.pendingCount >= 2) {
      state.level = instantaneousLevel
      state.pending = null
      state.pendingCount = 0
      this.stabilizedChangeCount += 1
    }
    return state.level
  }
}

export const sharedLaneCongestionStateResolver = new LaneCongestionStateResolver()
