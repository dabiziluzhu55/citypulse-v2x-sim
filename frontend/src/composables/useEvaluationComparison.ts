import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import type { MetricsTimeseriesPoint, MetricsTimeseriesResponse } from '../types/metrics'
import type { SimulationSnapshot, StartSimulationRequest } from '../types/simulation'

const STORAGE_KEY = 'citypulse.evaluation_comparison.v1'
const STORAGE_VERSION = 1
const MAX_GROUPS = 8
const MAX_POINTS_PER_RUN = 360

interface StoredRun {
  sessionId: string
  algorithm: string
  updatedAt: number
  points: MetricsTimeseriesPoint[]
}

interface StoredGroup {
  fingerprint: string
  updatedAt: number
  runs: Record<string, StoredRun>
}

interface StoredComparison {
  version: 1
  groups: StoredGroup[]
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, entry]) => [key, stableValue(entry)]),
  )
}

function normalizedDisturbanceTargets(payload: StartSimulationRequest): unknown[] {
  return payload.disturbance_targets
    .map(({ event_id: _runtimeEventId, ...target }) => stableValue(target))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)))
}

export function createScenarioFingerprint(
  payload: StartSimulationRequest,
  intersectionId: string,
): string {
  return JSON.stringify(stableValue({
    version: STORAGE_VERSION,
    intersection_id: intersectionId,
    scenario_preset_id: payload.scenario_preset_id,
    period: payload.period,
    origins: payload.origins,
    window_start_seconds: payload.window_start_seconds,
    duration_seconds: payload.duration_seconds,
    disturbance_targets: normalizedDisturbanceTargets(payload),
    seed: payload.seed,
    step_length: payload.step_length,
    snapshot_interval_seconds: payload.snapshot_interval_seconds,
    realtime: payload.realtime,
    gui: payload.gui,
  }))
}

function emptyStore(): StoredComparison {
  return { version: STORAGE_VERSION, groups: [] }
}

function readStore(): StoredComparison {
  if (typeof localStorage === 'undefined') return emptyStore()
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as StoredComparison | null
    if (parsed?.version === STORAGE_VERSION && Array.isArray(parsed.groups)) return parsed
  } catch {
    localStorage.removeItem(STORAGE_KEY)
  }
  return emptyStore()
}

function evaluationPoint(snapshot: SimulationSnapshot): MetricsTimeseriesPoint | null {
  const evaluation = snapshot.evaluation ?? snapshot.metrics.evaluation
  if (!evaluation) return null
  return {
    time: snapshot.elapsed_seconds,
    algorithm: evaluation.algorithm,
    avg_waiting_time: evaluation.avg_waiting_time,
    avg_travel_time: evaluation.avg_travel_time,
    avg_queue_length: evaluation.avg_queue_length,
    throughput: evaluation.throughput,
    fuel_consumption: evaluation.fuel_consumption,
  }
}

export function appendRealEvaluationPoint(
  points: MetricsTimeseriesPoint[],
  point: MetricsTimeseriesPoint,
  limit = MAX_POINTS_PER_RUN,
): MetricsTimeseriesPoint[] {
  const next = [...points]
  const existingIndex = next.findIndex((entry) => Math.abs(entry.time - point.time) < 1e-6)
  if (existingIndex >= 0) next[existingIndex] = point
  else next.push(point)
  next.sort((left, right) => left.time - right.time)
  return next.slice(-limit)
}

export function useEvaluationComparison(
  sessionId: Ref<string>,
  snapshot: Ref<SimulationSnapshot | null>,
) {
  const store = ref<StoredComparison>(readStore())
  const activeFingerprint = ref('')
  let persistTimer: ReturnType<typeof setTimeout> | null = null

  function findFingerprintBySession(nextSessionId: string): string {
    if (!nextSessionId) return ''
    return store.value.groups.find((group) => (
      Object.values(group.runs).some((run) => run.sessionId === nextSessionId)
    ))?.fingerprint ?? ''
  }

  function persistSoon() {
    if (typeof localStorage === 'undefined') return
    if (persistTimer !== null) clearTimeout(persistTimer)
    persistTimer = setTimeout(() => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store.value))
      persistTimer = null
    }, 400)
  }

  function beginRun(
    nextSessionId: string,
    payload: StartSimulationRequest,
    intersectionId: string,
  ): void {
    const fingerprint = createScenarioFingerprint(payload, intersectionId)
    const now = Date.now()
    let group = store.value.groups.find((entry) => entry.fingerprint === fingerprint)
    if (!group) {
      group = { fingerprint, updatedAt: now, runs: {} }
      store.value.groups.push(group)
    }
    group.updatedAt = now
    group.runs[payload.control_mode] = {
      sessionId: nextSessionId,
      algorithm: payload.control_mode,
      updatedAt: now,
      points: [],
    }
    store.value.groups = [...store.value.groups]
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .slice(0, MAX_GROUPS)
    activeFingerprint.value = fingerprint
    persistSoon()
  }

  function ingest(next: SimulationSnapshot | null): void {
    if (!next) return
    const point = evaluationPoint(next)
    if (!point?.algorithm) return
    let fingerprint = findFingerprintBySession(next.session_id)
    if (!fingerprint) {
      fingerprint = `restored-session:${next.session_id}`
      store.value.groups.unshift({
        fingerprint,
        updatedAt: Date.now(),
        runs: {},
      })
      store.value.groups = store.value.groups.slice(0, MAX_GROUPS)
    }
    activeFingerprint.value = fingerprint
    const group = store.value.groups.find((entry) => entry.fingerprint === fingerprint)
    if (!group) return
    const now = Date.now()
    const run = group.runs[point.algorithm] ?? {
      sessionId: next.session_id,
      algorithm: point.algorithm,
      updatedAt: now,
      points: [],
    }
    if (run.sessionId !== next.session_id) return
    run.updatedAt = now
    run.points = appendRealEvaluationPoint(run.points, point)
    group.runs[point.algorithm] = run
    group.updatedAt = now
    store.value.groups = [...store.value.groups]
    persistSoon()
  }

  const activeGroup = computed(() => (
    store.value.groups.find((group) => group.fingerprint === activeFingerprint.value) ?? null
  ))
  const timeseries = computed<MetricsTimeseriesResponse>(() => ({
    run_id: sessionId.value || activeFingerprint.value,
    series: Object.values(activeGroup.value?.runs ?? {})
      .flatMap((run) => run.points)
      .sort((left, right) => left.time - right.time || String(left.algorithm).localeCompare(String(right.algorithm))),
  }))
  const availableAlgorithms = computed(() => new Set(
    Object.values(activeGroup.value?.runs ?? {})
      .filter((run) => run.points.length > 0)
      .map((run) => run.algorithm),
  ))
  const hasComparisonData = computed(() => store.value.groups.some((group) => (
    Object.values(group.runs).some((run) => run.points.length > 0)
  )))

  watch(sessionId, (next) => {
    const restored = findFingerprintBySession(next)
    activeFingerprint.value = restored
  }, { immediate: true })
  watch(snapshot, ingest)
  onScopeDispose(() => {
    if (persistTimer !== null && typeof localStorage !== 'undefined') {
      clearTimeout(persistTimer)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store.value))
    }
  })

  return {
    timeseries,
    availableAlgorithms,
    hasComparisonData,
    beginRun,
  }
}
