import { METRICS_ALGORITHMS } from '../constants/metricsEvaluation.ts'
import type { EvaluationComparisonRun, ScenarioComparisonContractV3 } from '../composables/useEvaluationComparison.ts'
import {
  formatScenarioPresetLabel,
  formatSimulationPeriodLabel,
  formatSimulationWindow,
} from './scenarioDisplay.ts'

export const EVALUATION_REPORT_ALGORITHMS = METRICS_ALGORITHMS.map((item) => item.id)

export interface EvaluationReportRunPayload {
  algorithm: (typeof EVALUATION_REPORT_ALGORITHMS)[number]
  session_id: string | null
}

export interface EvaluationReportScenarioPayload {
  scenario_preset_id: string
  period: string
  window_start_seconds: number
  duration_seconds: number
}

export interface EvaluationReportRequest {
  scenario?: EvaluationReportScenarioPayload
  runs: EvaluationReportRunPayload[]
}

export function hasFinishedComparisonRun(runs: readonly EvaluationComparisonRun[]): boolean {
  return runs.some((run) => Boolean(run.sessionId) && run.finished === true)
}

export function buildEvaluationReportRequest(
  contract: ScenarioComparisonContractV3 | null,
  runs: readonly EvaluationComparisonRun[],
): EvaluationReportRequest {
  const byAlgorithm = new Map(runs.map((run) => [run.algorithm, run]))
  const payload: EvaluationReportRequest = {
    runs: EVALUATION_REPORT_ALGORITHMS.map((algorithm) => ({
      algorithm,
      session_id: byAlgorithm.get(algorithm)?.sessionId?.trim() || null,
    })),
  }
  if (contract) {
    payload.scenario = {
      scenario_preset_id: contract.scenario_preset_id,
      period: contract.period,
      window_start_seconds: contract.window_start_seconds,
      duration_seconds: contract.duration_seconds,
    }
  }
  return payload
}

export function buildEvaluationReportFilename(
  contract: ScenarioComparisonContractV3 | null,
): string {
  if (!contract) return '管控评估结果.pdf'
  const window = formatSimulationWindow(
    contract.period,
    contract.window_start_seconds,
    contract.duration_seconds,
  ).replaceAll(':', '-')
  return [
    formatScenarioPresetLabel(contract.scenario_preset_id),
    formatSimulationPeriodLabel(contract.period),
    window,
    '管控评估结果.pdf',
  ].join('_')
}

export function filenameFromContentDisposition(disposition: string | null): string | null {
  if (!disposition) return null
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) return decodeURIComponent(encoded)
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  return plain ?? null
}
