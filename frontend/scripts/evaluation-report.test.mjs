import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { createScenarioFingerprint } from '../src/composables/useEvaluationComparison.ts'
import {
  buildEvaluationReportFilename,
  buildEvaluationReportRequest,
  EVALUATION_REPORT_ALGORITHMS,
  filenameFromContentDisposition,
  hasFinishedComparisonRun,
} from '../src/utils/evaluationReport.ts'

const rightSidebarSource = await readFile(
  new URL('../src/components/dashboard/RightSidebarPanel.vue', import.meta.url),
  'utf8',
)
const apiSource = await readFile(
  new URL('../src/api/evaluationReport.ts', import.meta.url),
  'utf8',
)

function payload(overrides = {}) {
  return {
    scenario_preset_id: 'xiongan_20',
    period: 'morning_peak',
    origins: { demo_2: ['west'] },
    window_start_seconds: 0,
    duration_seconds: 900,
    control_mode: 'fixed',
    seed: 42,
    step_length: 0.2,
    realtime: true,
    gui: false,
    snapshot_interval_seconds: 0.5,
    disturbance_targets: [],
    playback_speed: 1,
    ...overrides,
  }
}

test('export button lights up only after a finished evaluation exists', () => {
  assert.equal(hasFinishedComparisonRun([]), false)
  assert.equal(hasFinishedComparisonRun([
    { algorithm: 'fixed', sessionId: 'fixed-1', finished: false },
  ]), false)
  assert.equal(hasFinishedComparisonRun([
    { algorithm: 'fixed', sessionId: 'fixed-1', finished: true },
    { algorithm: 'cov2x', sessionId: 'cov2x-1', finished: false },
  ]), true)
})

test('export request always includes six current-group algorithms', () => {
  const fingerprint = createScenarioFingerprint(payload(), ['demo_2'])
  const contract = JSON.parse(fingerprint)
  const request = buildEvaluationReportRequest(contract, [
    { algorithm: 'fixed', sessionId: 'fixed-1', finished: true },
    { algorithm: 'cov2x', sessionId: 'cov2x-1', finished: false },
  ])
  assert.deepEqual(EVALUATION_REPORT_ALGORITHMS, [
    'fixed', 'max_pressure', 'sotl', 'ippo', 'mappo', 'cov2x',
  ])
  assert.deepEqual(request.runs, [
    { algorithm: 'fixed', session_id: 'fixed-1' },
    { algorithm: 'max_pressure', session_id: null },
    { algorithm: 'sotl', session_id: null },
    { algorithm: 'ippo', session_id: null },
    { algorithm: 'mappo', session_id: null },
    { algorithm: 'cov2x', session_id: 'cov2x-1' },
  ])
  assert.deepEqual(request.scenario, {
    scenario_preset_id: 'xiongan_20',
    period: 'morning_peak',
    window_start_seconds: 0,
    duration_seconds: 900,
  })
})

test('a new comparison group does not export the previous group sessions', () => {
  const nextContract = JSON.parse(createScenarioFingerprint(payload({
    period: 'evening_peak',
    duration_seconds: 600,
  }), ['demo_2']))
  const request = buildEvaluationReportRequest(nextContract, [])
  assert.equal(request.runs.every((run) => run.session_id === null), true)
  assert.equal(hasFinishedComparisonRun([]), false)
  assert.equal(request.scenario?.period, 'evening_peak')
})

test('filename prefers Content-Disposition and falls back to the scene window', () => {
  const contract = JSON.parse(createScenarioFingerprint(payload(), ['demo_2']))
  assert.equal(
    buildEvaluationReportFilename(contract),
    '雄安20路口路网_早高峰_07-00-07-15_管控评估结果.pdf',
  )
  assert.equal(
    filenameFromContentDisposition(
      'attachment; filename="control-evaluation.pdf"; filename*=UTF-8\'\'%E7%AE%A1%E6%8E%A7%E8%AF%84%E4%BC%B0%E7%BB%93%E6%9E%9C.pdf',
    ),
    '管控评估结果.pdf',
  )
})

test('right sidebar no longer downloads frontend timeseries JSON', () => {
  assert.match(rightSidebarSource, /hasFinishedComparisonRun\(props\.comparisonRuns\)/)
  assert.doesNotMatch(rightSidebarSource, /runId\.trim\(\) && hasRealData/)
  assert.doesNotMatch(rightSidebarSource, /application\/json/)
  assert.doesNotMatch(rightSidebarSource, /control-evaluation-.*\.json/)
  assert.doesNotMatch(rightSidebarSource, /请先启动仿真并等待评估数据返回/)
  assert.match(rightSidebarSource, /导出当前场景已完成算法的终态评估报告/)
  assert.match(rightSidebarSource, /当前场景暂无已完成的终态评估结果/)
  assert.match(rightSidebarSource, /正在生成评估报告/)
  assert.match(apiSource, /\/evaluation-reports\/pdf/)
  assert.match(apiSource, /application\/pdf/)
})
