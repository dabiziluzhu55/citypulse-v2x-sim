import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('road continuity audit covers all intersections without eligible topology gaps', async () => {
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
  assert.ok(report.intersections.every((item) => item.status === 'pass'))
})
