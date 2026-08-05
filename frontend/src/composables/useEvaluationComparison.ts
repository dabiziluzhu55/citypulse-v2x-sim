import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import { fetchSimulationMetrics } from '../api/simulation.ts'
import type { MetricsTimeseriesPoint, MetricsTimeseriesResponse } from '../types/metrics'
import {
  TERMINAL_SIMULATION_STATES,
  type SimulationEvaluation,
  type SimulationSnapshot,
  type StartSimulationRequest,
} from '../types/simulation.ts'

const STORAGE_KEY = 'citypulse.evaluation_comparison.v2'
const LEGACY_STORAGE_KEY = 'citypulse.evaluation_comparison.v1'
const STORAGE_VERSION = 2
const MAX_GROUPS = 8
const MAX_POINTS_PER_RUN = 200
export const EVALUATION_BUCKET_SECONDS = 5
export const EVALUATION_TIME_LIMIT_SECONDS = 15 * 60

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
  version: 2
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

export function comparisonChangeRequiresConfirmation(
  activeFingerprint: string,
  candidateFingerprint: string,
  hasActiveData: boolean,
): boolean {
  return Boolean(
    hasActiveData
    && activeFingerprint
    && candidateFingerprint !== activeFingerprint,
  )
}

function emptyStore(): StoredComparison {
  return { version: STORAGE_VERSION, groups: [] }
}

function readStore(): StoredComparison {
  if (typeof localStorage === 'undefined') return emptyStore()
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as StoredComparison | null
    if (parsed?.version === STORAGE_VERSION && Array.isArray(parsed.groups)) return parsed
  } catch {
    localStorage.removeItem(STORAGE_KEY)
  }
  return emptyStore()
}

function boundedEvaluationTime(value: number): number {
  return Math.min(EVALUATION_TIME_LIMIT_SECONDS, Math.max(0, value))
}

export function evaluationPoint(
  snapshot: SimulationSnapshot,
  suppliedEvaluation?: SimulationEvaluation,
): MetricsTimeseriesPoint | null {
  const evaluation = suppliedEvaluation ?? snapshot.evaluation ?? snapshot.metrics.evaluation
  if (!evaluation) return null
  const terminal = TERMINAL_SIMULATION_STATES.includes(snapshot.state)
  if (terminal && !evaluation.finished) return null
  const rawTime = evaluation.finished && snapshot.state === 'COMPLETED'
    ? snapshot.duration_seconds
    : snapshot.elapsed_seconds
  const boundedTime = boundedEvaluationTime(rawTime)
  const time = evaluation.finished
    ? boundedTime
    : Math.floor(boundedTime / EVALUATION_BUCKET_SECONDS) * EVALUATION_BUCKET_SECONDS
  return {
    time,
    algorithm: evaluation.algorithm,
    avg_waiting_time: evaluation.avg_waiting_time,
    avg_travel_time: evaluation.avg_travel_time,
    avg_queue_length: evaluation.avg_queue_length,
    throughput: evaluation.throughput,
    fuel_consumption: evaluation.fuel_consumption,
    finished: evaluation.finished,
    metric_sources: { ...(evaluation.metric_sources ?? {}) },
    warnings: [...(evaluation.warnings ?? [])],
  }
}

export function requiresFinalEvaluationRecovery(snapshot: SimulationSnapshot): boolean {
  const evaluation = snapshot.evaluation ?? snapshot.metrics.evaluation
  return TERMINAL_SIMULATION_STATES.includes(snapshot.state) && !evaluation?.finished
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
  const finalizationWarning = ref<string | null>(null)
  let persistTimer: ReturnType<typeof setTimeout> | null = null
  let finalizationRequest = 0

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

  function storePoint(next: SimulationSnapshot, point: MetricsTimeseriesPoint): void {
    if (!point.algorithm) return
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

  function waitForFinalization(): Promise<void> {
    return new Promise((resolve) => window.setTimeout(resolve, 500))
  }

  async function recoverFinalEvaluation(next: SimulationSnapshot): Promise<void> {
    const request = ++finalizationRequest
    finalizationWarning.value = '正在汇总 TripInfo 终态指标'
    let lastError: unknown = null
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const evaluation = await fetchSimulationMetrics(next.session_id)
        if (request !== finalizationRequest) return
        if (evaluation.finished) {
          const point = evaluationPoint(next, evaluation)
          if (point) storePoint(next, point)
          finalizationWarning.value = null
          return
        }
      } catch (cause) {
        lastError = cause
      }
      await waitForFinalization()
    }
    if (request !== finalizationRequest) return
    finalizationWarning.value = lastError instanceof Error
      ? `终态指标汇总失败：${lastError.message}`
      : '终态指标在 10 秒内未完成，未记录临时结果'
  }

  function ingest(next: SimulationSnapshot | null): void {
    if (!next) return
    if (requiresFinalEvaluationRecovery(next)) {
      void recoverFinalEvaluation(next)
      return
    }
    finalizationRequest += 1
    finalizationWarning.value = null
    const point = evaluationPoint(next)
    if (point) storePoint(next, point)
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
  const hasActiveComparisonData = computed(() => Object.values(activeGroup.value?.runs ?? {})
    .some((run) => run.points.length > 0))

  function resetForConfiguration(fingerprint: string): void {
    const now = Date.now()
    store.value = {
      version: STORAGE_VERSION,
      groups: fingerprint ? [{ fingerprint, updatedAt: now, runs: {} }] : [],
    }
    activeFingerprint.value = fingerprint
    persistSoon()
  }

  watch(sessionId, (next) => {
    finalizationRequest += 1
    finalizationWarning.value = null
    const restored = findFingerprintBySession(next)
    activeFingerprint.value = restored
  }, { immediate: true })
  watch(snapshot, ingest)
  onScopeDispose(() => {
    finalizationRequest += 1
    if (persistTimer !== null && typeof localStorage !== 'undefined') {
      clearTimeout(persistTimer)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store.value))
    }
  })

  return {
    timeseries,
    availableAlgorithms,
    activeFingerprint,
    hasComparisonData,
    hasActiveComparisonData,
    finalizationWarning,
    beginRun,
    resetForConfiguration,
  }
}
