import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { EVALUATION_METRICS } from '../src/constants/metricsEvaluation.ts'
import {
  ADVANTAGE_METRIC_SPECS,
  buildAdvantageMetrics,
  calculateImprovement,
  createEmptyPositiveAdvantageCache,
  findComparableBaselinePoint,
  formatActiveVehicleCount,
  formatAdvantagePercent,
  isPositiveAdvantage,
  positiveAdvantageCacheScope,
  resolveDisplayedAdvantageMetric,
  TRAFFIC_STATE_COLORS,
  trafficStateColor,
  updatePositiveAdvantageCache,
} from '../src/utils/evaluationComparisonMetrics.ts'

const rightSidebarSource = await readFile(
  new URL('../src/components/dashboard/RightSidebarPanel.vue', import.meta.url),
  'utf8',
)
const layoutSource = await readFile(
  new URL('../src/constants/rightSidebarLayout.ts', import.meta.url),
  'utf8',
)

function point(overrides = {}) {
  return {
    time: 0,
    algorithm: 'fixed',
    avg_waiting_time: null,
    avg_queue_length: null,
    throughput: null,
    finished: false,
    ...overrides,
  }
}

test('nine chart metrics stay in the required order', () => {
  assert.deepEqual(EVALUATION_METRICS.map((item) => [item.key, item.field, item.title]), [
    ['path_speed', 'path_avg_speed_kmh', '平均行程速度'],
    ['stops', 'avg_stops_per_vehicle', '平均停车次数'],
    ['max_queue', 'regional_max_queue_length_m', '最大排队长度'],
    ['travel_time', 'avg_travel_time', '平均行程时间'],
    ['waiting_time', 'avg_waiting_time', '平均等待时间'],
    ['throughput', 'throughput', '吞吐流率'],
    ['spillback', 'spillback_rate', '溢流率'],
    ['hard_braking', 'hard_braking_rate', '急刹车率'],
    ['fuel_intensity', 'fuel_intensity_L_per_100km', '百公里油耗强度'],
  ])
})

test('traffic state colors follow the national standard RGB values', () => {
  assert.deepEqual(TRAFFIC_STATE_COLORS, {
    畅通: '#008000',
    基本畅通: '#99CC00',
    轻度拥堵: '#FFFF00',
    中度拥堵: '#FF9900',
    严重拥堵: '#FF0000',
  })
  assert.equal(trafficStateColor('畅通'), '#008000')
  assert.equal(trafficStateColor('基本畅通'), '#99CC00')
  assert.equal(trafficStateColor('轻度拥堵'), '#FFFF00')
  assert.equal(trafficStateColor('中度拥堵'), '#FF9900')
  assert.equal(trafficStateColor('严重拥堵'), '#FF0000')
  assert.equal(trafficStateColor(null), null)
  assert.equal(trafficStateColor('未知'), null)
})

test('active vehicle count shows 0 and never substitutes a dash', () => {
  assert.equal(formatActiveVehicleCount(0), '0')
  assert.equal(formatActiveVehicleCount(386), '386')
  assert.equal(formatActiveVehicleCount(null), '—')
  assert.equal(formatActiveVehicleCount(undefined), '—')
})

test('fixed unfinished leaves all advantage cells empty', () => {
  const metrics = buildAdvantageMetrics([
    point({
      algorithm: 'fixed',
      time: 300,
      regional_max_queue_length_m: 100,
      avg_waiting_time: 30,
      throughput: 1000,
      path_avg_speed_kmh: 30,
      finished: false,
    }),
    point({
      algorithm: 'cov2x',
      time: 300,
      regional_max_queue_length_m: 70,
      avg_waiting_time: 20,
      throughput: 1200,
      path_avg_speed_kmh: 36,
      finished: false,
    }),
  ], 'cov2x', 'RUNNING')
  assert.equal(metrics.every((item) => item.value == null && item.direction == null), true)
  assert.deepEqual(metrics.map((item) => formatAdvantagePercent(item.value)), ['—', '—', '—', '—'])
})

test('same-progress max queue improvement is a green down 30 percent', () => {
  const metrics = buildAdvantageMetrics([
    point({
      algorithm: 'fixed',
      time: 300,
      regional_max_queue_length_m: 100,
      finished: true,
    }),
    point({
      algorithm: 'fixed',
      time: 900,
      regional_max_queue_length_m: 150,
      finished: true,
    }),
    point({
      algorithm: 'cov2x',
      time: 300,
      regional_max_queue_length_m: 70,
      finished: false,
    }),
  ], 'cov2x', 'RUNNING')
  const queue = metrics.find((item) => item.key === 'max_queue')
  assert.equal(queue.value.toFixed(1), '30.0')
  assert.equal(queue.direction, 'down')
  assert.equal(queue.improved, true)
  assert.equal(formatAdvantagePercent(queue.value), '30.0%')
})

test('never compares a running algorithm against a later fixed final value', () => {
  const baseline = findComparableBaselinePoint([
    point({ algorithm: 'fixed', time: 300, regional_max_queue_length_m: 100, finished: false }),
    point({ algorithm: 'fixed', time: 900, regional_max_queue_length_m: 150, finished: true }),
  ], 300, false)
  assert.equal(baseline.time, 300)
  assert.equal(baseline.regional_max_queue_length_m, 100)

  const metrics = buildAdvantageMetrics([
    point({ algorithm: 'fixed', time: 300, regional_max_queue_length_m: 100, finished: true }),
    point({ algorithm: 'fixed', time: 900, regional_max_queue_length_m: 150, finished: true }),
    point({ algorithm: 'cov2x', time: 300, regional_max_queue_length_m: 90, finished: false }),
  ], 'cov2x', 'RUNNING')
  const queue = metrics.find((item) => item.key === 'max_queue')
  assert.equal(queue.value.toFixed(1), '10.0')
})

test('does not use a future fixed bucket when the exact time is missing', () => {
  const baseline = findComparableBaselinePoint([
    point({ algorithm: 'fixed', time: 295, regional_max_queue_length_m: 80 }),
    point({ algorithm: 'fixed', time: 305, regional_max_queue_length_m: 200 }),
  ], 300, false)
  assert.equal(baseline.time, 295)
  assert.equal(baseline.regional_max_queue_length_m, 80)
})

test('throughput improvement is a green up 20 percent', () => {
  const metrics = buildAdvantageMetrics([
    point({ algorithm: 'fixed', time: 300, throughput: 1000, finished: true }),
    point({ algorithm: 'cov2x', time: 300, throughput: 1200, finished: false }),
  ], 'cov2x', 'RUNNING')
  const throughput = metrics.find((item) => item.key === 'throughput')
  assert.equal(throughput.value.toFixed(1), '20.0')
  assert.equal(throughput.direction, 'up')
  assert.equal(throughput.improved, true)
})

test('path speed improvement is a green up 20 percent', () => {
  const metrics = buildAdvantageMetrics([
    point({ algorithm: 'fixed', time: 300, path_avg_speed_kmh: 30, finished: true }),
    point({ algorithm: 'cov2x', time: 300, path_avg_speed_kmh: 36, finished: false }),
  ], 'cov2x', 'RUNNING')
  const speed = metrics.find((item) => item.key === 'path_speed')
  assert.equal(speed.label, '平均行程速度')
  assert.equal(speed.value.toFixed(1), '20.0')
  assert.equal(speed.direction, 'up')
  assert.equal(speed.improved, true)
})

test('worse waiting time is a red up 20 percent', () => {
  const metrics = buildAdvantageMetrics([
    point({ algorithm: 'fixed', time: 300, avg_waiting_time: 30, finished: true }),
    point({ algorithm: 'max_pressure', time: 300, avg_waiting_time: 36, finished: false }),
  ], 'max_pressure', 'RUNNING')
  const waiting = metrics.find((item) => item.key === 'waiting_time')
  assert.equal(waiting.value.toFixed(1), '-20.0')
  assert.equal(waiting.direction, 'up')
  assert.equal(waiting.improved, false)
  assert.equal(formatAdvantagePercent(waiting.value), '20.0%')
})

test('zero or invalid fixed values stay as an em dash without NaN', () => {
  const zero = calculateImprovement(12, 0, true)
  const missing = calculateImprovement(12, null, true)
  const nan = calculateImprovement(12, Number.NaN, true)
  const infinite = calculateImprovement(12, Number.POSITIVE_INFINITY, false)
  for (const result of [zero, missing, nan, infinite]) {
    assert.equal(result.value, null)
    assert.equal(result.direction, null)
    assert.equal(Number.isFinite(result.value), false)
    assert.equal(formatAdvantagePercent(result.value), '—')
  }

  const metrics = buildAdvantageMetrics([
    point({ algorithm: 'fixed', time: 300, regional_max_queue_length_m: 0, finished: true }),
    point({ algorithm: 'sotl', time: 300, regional_max_queue_length_m: 10, finished: false }),
  ], 'sotl', 'RUNNING')
  assert.equal(metrics.find((item) => item.key === 'max_queue').value, null)
})

test('finished current algorithm compares finals instead of the live bucket', () => {
  const metrics = buildAdvantageMetrics([
    point({ algorithm: 'fixed', time: 300, regional_max_queue_length_m: 100, finished: false }),
    point({ algorithm: 'fixed', time: 900, regional_max_queue_length_m: 150, finished: true }),
    point({ algorithm: 'ippo', time: 300, regional_max_queue_length_m: 90, finished: false }),
    point({ algorithm: 'ippo', time: 900, regional_max_queue_length_m: 120, finished: true }),
  ], 'ippo', 'COMPLETED')
  const queue = metrics.find((item) => item.key === 'max_queue')
  assert.equal(queue.value.toFixed(1), '20.0')
})

test('right sidebar uses one switchable chart and the new layout blocks', () => {
  assert.match(layoutSource, /advantage:/)
  assert.match(layoutSource, /trafficOverview:/)
  assert.match(layoutSource, /legend:/)
  assert.match(layoutSource, /chart:/)
  assert.match(layoutSource, /exportButton:/)
  assert.doesNotMatch(layoutSource, /metrics:\s*\[/)
  assert.equal(rightSidebarSource.includes('v-for="metric in EVALUATION_METRICS"'), false)
  assert.match(rightSidebarSource, /const chartRef/)
  assert.match(rightSidebarSource, /交通效能提升/)
  assert.match(rightSidebarSource, /算法对比/)
  assert.match(rightSidebarSource, /实时交通状态/)
  assert.match(rightSidebarSource, /实时车辆数/)
  assert.match(rightSidebarSource, /displayedAdvantageMetrics/)
  assert.match(rightSidebarSource, /lastPositiveCov2xMetrics/)
  assert.doesNotMatch(rightSidebarSource, /trafficStateColor/)
  assert.doesNotMatch(rightSidebarSource, /trafficStateStyle/)
  assert.doesNotMatch(rightSidebarSource, /:style="trafficStateStyle"/)
  for (const color of ['#008000', '#99CC00', '#FFFF00', '#FF9900', '#FF0000']) {
    assert.doesNotMatch(rightSidebarSource, new RegExp(`style="color:${color}"`))
  }
  assert.match(rightSidebarSource, /grid-template-columns: repeat\(3, 1fr\)/)
  assert.match(rightSidebarSource, /el-button-group/)
  assert.match(layoutSource, /height: 188/)
  assert.doesNotMatch(rightSidebarSource, /@element-plus\/icons-vue/)
  assert.deepEqual(ADVANTAGE_METRIC_SPECS.map((item) => item.field), [
    'regional_max_queue_length_m',
    'avg_waiting_time',
    'throughput',
    'path_avg_speed_kmh',
  ])
})

function metricAt(value, extras = {}) {
  const improved = typeof value === 'number' ? (value > 0 ? true : value < 0 ? false : null) : null
  return {
    key: extras.key ?? 'max_queue',
    label: extras.label ?? '最大排队长度',
    value,
    direction: extras.direction ?? (typeof value === 'number' && value !== 0 ? 'down' : null),
    improved,
  }
}

function playPositiveHold(steps, algorithmId = 'cov2x') {
  let cache = createEmptyPositiveAdvantageCache()
  let displayed = null
  for (const step of steps) {
    const finished = step.finished === true
    cache = updatePositiveAdvantageCache(cache, [step.metric], { algorithmId, finished })
    displayed = resolveDisplayedAdvantageMetric(step.metric, cache[step.metric.key], {
      algorithmId,
      finished,
    })
  }
  return { cache, displayed }
}

test('CoV2X positive hold keeps the last meaningful positive live value', () => {
  const down = { direction: 'down' }
  const case1 = playPositiveHold([
    { metric: metricAt(10, down) },
    { metric: metricAt(-5, { direction: 'up' }) },
  ])
  assert.equal(case1.displayed.value, 10)

  const case2 = playPositiveHold([
    { metric: metricAt(10, down) },
    { metric: metricAt(7, down) },
  ])
  assert.equal(case2.displayed.value, 7)

  const case3 = playPositiveHold([
    { metric: metricAt(10, down) },
    { metric: metricAt(0, { direction: null }) },
  ])
  assert.equal(case3.displayed.value, 10)

  const case4 = playPositiveHold([{ metric: metricAt(-5, { direction: 'up' }) }])
  assert.equal(case4.displayed.value, null)
  assert.equal(formatAdvantagePercent(case4.displayed.value), '—')

  const case5 = playPositiveHold([{ metric: metricAt(null) }])
  assert.equal(case5.displayed.value, null)

  const case6 = playPositiveHold([
    { metric: metricAt(10, down) },
    { metric: metricAt(null) },
  ])
  assert.equal(case6.displayed.value, 10)

  const case7 = playPositiveHold([
    { metric: metricAt(10, down) },
    { metric: metricAt(18, down), finished: true },
  ])
  assert.equal(case7.displayed.value, 18)

  const case8 = playPositiveHold([
    { metric: metricAt(10, down) },
    { metric: metricAt(-3, { direction: 'up' }), finished: true },
  ])
  assert.equal(case8.displayed.value, -3)
  assert.equal(case8.displayed.improved, false)
})

test('CoV2X positive hold is not a historical maximum and ignores sub-epsilon noise', () => {
  assert.equal(isPositiveAdvantage(metricAt(0.04)), false)
  assert.equal(isPositiveAdvantage(metricAt(0.05)), true)
  const played = playPositiveHold([
    { metric: metricAt(12) },
    { metric: metricAt(18) },
    { metric: metricAt(14) },
    { metric: metricAt(-3, { direction: 'up' }) },
    { metric: metricAt(10) },
  ])
  assert.equal(played.displayed.value, 10)
})

test('positive hold does not apply to Max Pressure and clears across comparison groups', () => {
  const played = playPositiveHold([
    { metric: metricAt(10) },
    { metric: metricAt(-5, { direction: 'up' }) },
  ], 'max_pressure')
  assert.equal(played.displayed.value, -5)
  assert.equal(played.displayed.improved, false)

  const sameGroup = positiveAdvantageCacheScope({
    runId: 'run-a',
    algorithmId: 'cov2x',
    contractFingerprint: 'group-1',
    cov2xSessionId: 'cov-1',
    fixedSessionId: 'fixed-1',
    timeseriesEmpty: false,
  })
  const nextGroup = positiveAdvantageCacheScope({
    runId: 'run-a',
    algorithmId: 'cov2x',
    contractFingerprint: 'group-2',
    cov2xSessionId: 'cov-2',
    fixedSessionId: 'fixed-2',
    timeseriesEmpty: false,
  })
  assert.notEqual(sameGroup, nextGroup)
  assert.match(rightSidebarSource, /positiveHoldScopeKey/)
})
