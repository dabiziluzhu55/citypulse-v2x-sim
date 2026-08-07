import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import { fetchSimulationMetrics } from '../api/simulation.ts'
import type {
  EvaluationMetricKey,
  MetricPresentationStatus,
  MetricsTimeseriesPoint,
  MetricsTimeseriesResponse,
} from '../types/metrics'
import {
  TERMINAL_SIMULATION_STATES,
  type SimulationEvaluation,
  type SimulationSnapshot,
  type StartSimulationRequest,
} from '../types/simulation.ts'
import { simulationFuelIntensity } from '../utils/simulationEvaluation.ts'
import { scenarioPresetIntersectionIds } from '../utils/scenarioPresetRules.ts'

const STORAGE_KEY = 'citypulse.evaluation_comparison.v3'
const LEGACY_STORAGE_KEYS = [
  'citypulse.evaluation_comparison.v1',
  'citypulse.evaluation_comparison.v2',
]
const STORAGE_VERSION = 3
const MAX_GROUPS = 8
const MAX_POINTS_PER_RUN = 200
export const EVALUATION_BUCKET_SECONDS = 5
export const EVALUATION_TIME_LIMIT_SECONDS = 15 * 60

export interface StoredRun {
  sessionId: string
  algorithm: string
  updatedAt: number
  points: MetricsTimeseriesPoint[]
  state?: SimulationSnapshot['state']
  progress?: number
  lastSequence?: number
  startedAt?: number
  completedAt?: number | null
  error?: string | null
}

export function updateStoredRunFromSnapshot(
  run: StoredRun,
  next: SimulationSnapshot,
  now = Date.now(),
): StoredRun {
  const terminal = TERMINAL_SIMULATION_STATES.includes(next.state)
  return {
    ...run,
    state: next.state,
    progress: typeof next.progress === 'number'
      ? Math.min(1, Math.max(0, next.progress))
      : run.progress ?? 0,
    lastSequence: Math.max(run.lastSequence ?? -1, next.sequence),
    completedAt: terminal ? run.completedAt ?? now : null,
    error: next.error?.trim() || (next.state === 'FAILED' ? run.error ?? null : null),
    updatedAt: now,
  }
}

interface StoredGroup {
  fingerprint: string
  updatedAt: number
  runs: Record<string, StoredRun>
  readOnly?: boolean
}

interface StoredComparison {
  version: 3
  groups: StoredGroup[]
}

export interface ScenarioComparisonContractV3 {
  version: 3
  scenario_preset_id: string
  controlled_intersection_ids: string[]
  period: string
  window_start_seconds: number
  duration_seconds: number
  origins: unknown
  disturbance_targets: unknown[]
  seed: number
  step_length: number
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    const unique = new Map<string, unknown>()
    value.map(stableValue).forEach((entry) => unique.set(JSON.stringify(entry), entry))
    return [...unique.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, entry]) => entry)
  }
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
  controlledIntersectionIds: readonly string[] | string = scenarioPresetIntersectionIds(
    payload.scenario_preset_id,
  ),
): string {
  return JSON.stringify(createScenarioComparisonContract(payload, controlledIntersectionIds))
}

export function createScenarioComparisonContract(
  payload: StartSimulationRequest,
  controlledIntersectionIds: readonly string[] | string = scenarioPresetIntersectionIds(
    payload.scenario_preset_id,
  ),
): ScenarioComparisonContractV3 {
  const resolvedIntersectionIds = typeof controlledIntersectionIds === 'string'
    ? scenarioPresetIntersectionIds(payload.scenario_preset_id)
    : controlledIntersectionIds
  return stableValue({
    version: STORAGE_VERSION,
    scenario_preset_id: payload.scenario_preset_id,
    controlled_intersection_ids: [...new Set(resolvedIntersectionIds)].sort(),
    period: payload.period,
    origins: payload.origins,
    window_start_seconds: payload.window_start_seconds,
    duration_seconds: payload.duration_seconds,
    disturbance_targets: normalizedDisturbanceTargets(payload),
    seed: payload.seed,
    step_length: payload.step_length,
  }) as ScenarioComparisonContractV3
}

export function comparisonContractDifferences(
  activeFingerprint: string,
  candidateFingerprint: string,
): string[] {
  if (!activeFingerprint || !candidateFingerprint) return []
  try {
    const active = JSON.parse(activeFingerprint) as ScenarioComparisonContractV3
    const candidate = JSON.parse(candidateFingerprint) as ScenarioComparisonContractV3
    const differences: string[] = []
    const leadingLabels: Array<[keyof ScenarioComparisonContractV3, string]> = [
      ['scenario_preset_id', '仿真场景'],
      ['controlled_intersection_ids', '受控路口'],
      ['period', '交通时段'],
    ]
    const trailingLabels: Array<[keyof ScenarioComparisonContractV3, string]> = [
      ['origins', '交通需求起点'],
      ['disturbance_targets', '扰动事件'],
      ['seed', '随机种子'],
      ['step_length', '仿真步长'],
    ]
    const appendDifferences = (labels: Array<[keyof ScenarioComparisonContractV3, string]>) => {
      for (const [key, label] of labels) {
        if (JSON.stringify(active[key]) !== JSON.stringify(candidate[key])) {
          differences.push(
            `${label}：${comparisonContractValue(key, active[key])} → ${comparisonContractValue(key, candidate[key])}`,
          )
        }
      }
    }
    appendDifferences(leadingLabels)
    if (
      active.window_start_seconds !== candidate.window_start_seconds
      || active.duration_seconds !== candidate.duration_seconds
    ) {
      differences.push(`展示窗口：${comparisonWindow(active)} → ${comparisonWindow(candidate)}`)
    }
    appendDifferences(trailingLabels)
    return differences
  } catch {
    return ['仿真参数']
  }
}

function comparisonClock(totalSeconds: number): string {
  const normalized = Math.max(0, Math.round(totalSeconds))
  const hours = Math.floor(normalized / 3600) % 24
  const minutes = Math.floor((normalized % 3600) / 60)
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`
}

function comparisonWindow(contract: ScenarioComparisonContractV3): string {
  return `${comparisonClock(contract.window_start_seconds)}–${comparisonClock(
    contract.window_start_seconds + contract.duration_seconds,
  )}`
}

function comparisonContractValue(
  key: keyof ScenarioComparisonContractV3,
  value: unknown,
): string {
  if (key === 'period') {
    return ({ morning_peak: '早高峰', off_peak: '平峰', evening_peak: '晚高峰' } as Record<string, string>)[String(value)]
      ?? String(value)
  }
  if (key === 'controlled_intersection_ids') return (value as string[]).join('、') || '无'
  if (key === 'disturbance_targets') {
    const targets = value as Array<Record<string, unknown>>
    if (targets.length === 0) return '无'
    const eventLabels: Record<string, string> = {
      lane_closure: '车道封闭',
      speed_limit: '限速',
      accident: '事故',
      major_event_opening: '大型活动开场',
      major_event_closing: '大型活动散场',
    }
    return targets.map((target) => (
      `${String(target.intersection_id ?? '未知路口').replace(/^demo_(\d+)$/, '路口$1')} ${
        eventLabels[String(target.event_type)] ?? '扰动'
      }`
    )).join('、')
  }
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return JSON.stringify(value)
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
    LEGACY_STORAGE_KEYS.forEach((key) => localStorage.removeItem(key))
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

function metricStatus(
  value: number | null | undefined,
  finished: boolean,
): MetricPresentationStatus {
  if (typeof value === 'number') return finished ? 'final' : 'provisional'
  return finished ? 'unavailable' : 'pending'
}

function evaluationMetricStatuses(
  snapshot: SimulationSnapshot,
  evaluation: SimulationEvaluation,
): Record<EvaluationMetricKey, MetricPresentationStatus> {
  const fuelIntensity = simulationFuelIntensity(evaluation)
  const contradictoryWaitingZero = !evaluation.finished
    && evaluation.avg_waiting_time === 0
    && (
      (snapshot.metrics.total_waiting_time ?? 0) > 0
      || (snapshot.metrics.halting_vehicles ?? 0) > 0
    )
  const fuelExplicitlyUnavailable = fuelIntensity == null
    && (evaluation.warnings ?? []).some((warning) => (
      /燃油|fuel|powertrain|里程/i.test(warning)
      && /不可用|无法|缺少|不足|unavailable|missing|invalid/i.test(warning)
    ))
  return {
    queue: metricStatus(evaluation.avg_queue_length, evaluation.finished),
    waiting: contradictoryWaitingZero
      ? 'pending'
      : metricStatus(evaluation.avg_waiting_time, evaluation.finished),
    fuel: fuelExplicitlyUnavailable
      ? 'unavailable'
      : metricStatus(fuelIntensity, evaluation.finished),
  }
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
  const metricStatuses = evaluationMetricStatuses(snapshot, evaluation)
  const fuelIntensity = simulationFuelIntensity(evaluation)
  return {
    time,
    algorithm: evaluation.algorithm,
    avg_waiting_time: metricStatuses.waiting === 'pending'
      ? null
      : evaluation.avg_waiting_time,
    avg_travel_time: evaluation.avg_travel_time,
    avg_queue_length: evaluation.avg_queue_length,
    throughput: evaluation.throughput,
    fuel_consumption: fuelIntensity,
    fuel_intensity_L_per_100km: fuelIntensity,
    hard_braking_events: evaluation.hard_braking_events ?? null,
    hard_braking_rate: evaluation.hard_braking_rate ?? null,
    finished: evaluation.finished,
    metric_sources: { ...(evaluation.metric_sources ?? {}) },
    warnings: [...(evaluation.warnings ?? [])],
    metric_status: metricStatuses,
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
  const existingIndex = next.findIndex((entry) => (
    Math.abs(entry.time - point.time) < 1e-6
    || (
      point.finished
      && Math.floor(entry.time / EVALUATION_BUCKET_SECONDS)
        === Math.floor(point.time / EVALUATION_BUCKET_SECONDS)
    )
  ))
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
    controlledIntersectionIds?: readonly string[],
  ): void {
    const fingerprint = createScenarioFingerprint(payload, controlledIntersectionIds)
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
      state: 'STARTING',
      progress: 0,
      lastSequence: -1,
      startedAt: now,
      completedAt: null,
      error: null,
    }
    store.value.groups = [...store.value.groups]
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .slice(0, MAX_GROUPS)
    activeFingerprint.value = fingerprint
    persistSoon()
  }

  function storeRunSnapshot(next: SimulationSnapshot): void {
    const fingerprint = findFingerprintBySession(next.session_id)
    if (!fingerprint) return
    const group = store.value.groups.find((entry) => entry.fingerprint === fingerprint)
    if (!group || group.readOnly) return
    const runEntry = Object.entries(group.runs).find(([, entry]) => (
      entry.sessionId === next.session_id
    ))
    if (!runEntry) return
    const [algorithm, run] = runEntry
    const now = Date.now()
    group.runs[algorithm] = updateStoredRunFromSnapshot(run, next, now)
    group.updatedAt = now
    store.value.groups = [...store.value.groups]
    persistSoon()
  }

  function storePoint(next: SimulationSnapshot, point: MetricsTimeseriesPoint): void {
    if (!point.algorithm) return
    let fingerprint = findFingerprintBySession(next.session_id)
    if (!fingerprint) return
    activeFingerprint.value = fingerprint
    const group = store.value.groups.find((entry) => entry.fingerprint === fingerprint)
    if (!group || group.readOnly) return
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
    storeRunSnapshot(next)
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
    if (fingerprint && !store.value.groups.some((group) => group.fingerprint === fingerprint)) {
      store.value.groups.push({ fingerprint, updatedAt: now, runs: {} })
    }
    store.value.groups = [...store.value.groups]
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .slice(0, MAX_GROUPS)
    activeFingerprint.value = fingerprint
    persistSoon()
  }

  watch(sessionId, (next) => {
    finalizationRequest += 1
    finalizationWarning.value = null
    let restored = findFingerprintBySession(next)
    if (next && !restored) {
      restored = `unverified-session:${next}`
      if (!store.value.groups.some((group) => group.fingerprint === restored)) {
        store.value.groups.unshift({
          fingerprint: restored,
          updatedAt: Date.now(),
          runs: {},
          readOnly: true,
        })
        store.value.groups = store.value.groups.slice(0, MAX_GROUPS)
      }
    }
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
