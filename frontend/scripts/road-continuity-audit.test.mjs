import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('road continuity audit fails short gaps and reports unresolved long source gaps as warnings', async () => {
  const report = JSON.parse(await readFile(
    new URL('../reports/road-continuity-audit.json', import.meta.url),
    'utf8',
  ))
  assert.equal(report.schemaVersion, 1)
  assert.equal(report.summary.checked, 20)
  assert.equal(report.summary.failed, 0)
  assert.equal(report.summary.uncoveredEligibleConnections, 0)
  assert.ok(report.summary.roadJoints > 20)
  assert.equal(report.thresholds.minimumOverlapMeters, 0.5)
  assert.equal(report.thresholds.maximumSurfaceHeightDifferenceMeters, 0.02)
  assert.equal(report.thresholds.maximumVisibleBreakPixels, 3)
  assert.ok(report.intersections.every((item) => item.status !== 'fail'))
  assert.equal(report.summary.passed + report.summary.warnings, 20)
  const demo8 = report.intersections.find((item) => item.intersectionId === 'demo_8')
  assert.equal(demo8.status, 'pass')
  assert.equal(demo8.rejectedSourceGaps.length, 0)
  assert.ok(demo8.repairedAuthoritativeGaps.some((item) => item.junctionId === 'cluster_J154_J168'))
  assert.ok(demo8.repairedAuthoritativeGaps.some((item) => item.junctionId === 'cluster_J158_J167'))
})
