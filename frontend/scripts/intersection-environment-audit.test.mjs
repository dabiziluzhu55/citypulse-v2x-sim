import assert from 'node:assert/strict'
import test from 'node:test'

import { auditIntersectionEnvironments } from './audit-intersection-environments.mjs'

test('audits all intersection assets and preserves road transition overlap', async () => {
  const report = await auditIntersectionEnvironments()
  assert.equal(report.intersections.length, 20)
  assert.equal(report.summary.error, 0)
  assert.ok(report.summary.sparseBuildingSources.length > 0)
  assert.ok(report.intersections.every((row) => row.roads.minimumBoundaryOverlapMeters >= 4))
  assert.ok(report.intersections.every((row) => row.roads.frontendPatchVisible))
  assert.ok(report.intersections.every((row) => (
    ['local-overlap', 'baidu-base-dependent'].includes(row.roads.continuityClassification)
  )))
  assert.ok(report.intersections.every((row) => row.facilities.lamps > 0))
  assert.ok(report.summary.intersectionsWithRoadSurfaceExclusions.includes('demo_6'))
  assert.ok(!report.summary.intersectionsWithRoadSurfaceExclusions.includes('demo_4'))
  assert.ok(report.summary.roadSurfaceExclusionRanges > 0)
  assert.ok(report.intersections.every((row) => row.roads.buildingSurfaceExclusionRanges >= 0))
})
