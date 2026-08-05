import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  LANE_ARROW_MAX_LANE_WIDTH_RATIO,
  auditLaneDirectionArrows,
  createLaneArrowGeometry,
} from '../src/mapv/realistic/laneDirectionArrows.ts'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outputArgument = process.argv.find((value) => value.startsWith('--output='))
const output = resolve(root, outputArgument?.slice('--output='.length) || 'reports/lane-direction-arrow-audit.json')
const catalog = JSON.parse(await readFile(resolve(root, 'public/intersections/v3/catalog.json'), 'utf8'))

const intersections = []
const totals = {
  controlledLaneCount: 0,
  multiMovementLaneCount: 0,
  arrowCount: 0,
  patternCounts: {},
}

for (const entry of catalog.intersections) {
  const manifest = JSON.parse(await readFile(
    resolve(root, `public/intersections/v3/${entry.intersectionId}/manifest.json`),
    'utf8',
  ))
  const audit = auditLaneDirectionArrows(manifest)
  const placementErrors = []
  for (const arrow of audit.arrows) {
    const geometry = createLaneArrowGeometry(arrow.pattern)
    const width = (geometry.boundingBox.max.x - geometry.boundingBox.min.x) * arrow.scale
    geometry.dispose()
    if (width > arrow.laneWidth * LANE_ARROW_MAX_LANE_WIDTH_RATIO + 1e-6) {
      placementErrors.push(`${arrow.key}: width exceeds 80% of lane`)
    }
    totals.patternCounts[arrow.pattern] = (totals.patternCounts[arrow.pattern] ?? 0) + 1
  }
  totals.controlledLaneCount += audit.controlledLaneCount
  totals.multiMovementLaneCount += audit.multiMovementLaneCount
  totals.arrowCount += audit.arrows.length
  const errors = [...audit.unsupported, ...placementErrors]
  if (audit.arrows.length !== audit.controlledLaneCount) errors.push('one-arrow-per-lane contract failed')
  intersections.push({
    intersectionId: entry.intersectionId,
    controlledLaneCount: audit.controlledLaneCount,
    multiMovementLaneCount: audit.multiMovementLaneCount,
    arrowCount: audit.arrows.length,
    patternCounts: Object.fromEntries([...new Set(audit.arrows.map((arrow) => arrow.pattern))]
      .sort()
      .map((pattern) => [pattern, audit.arrows.filter((arrow) => arrow.pattern === pattern).length])),
    warnings: audit.warnings,
    errors,
    status: errors.length === 0 ? 'pass' : 'fail',
  })
}

const baselineErrors = []
if (totals.controlledLaneCount !== 120) baselineErrors.push(`expected 120 controlled lanes, got ${totals.controlledLaneCount}`)
if (totals.multiMovementLaneCount !== 96) baselineErrors.push(`expected 96 multi-movement lanes, got ${totals.multiMovementLaneCount}`)
if (totals.arrowCount !== 120) baselineErrors.push(`expected 120 arrows, got ${totals.arrowCount}`)
const report = {
  schemaVersion: 1,
  generatedAt: new Date().toISOString(),
  sourceSha256: catalog.sourceSha256,
  contract: {
    controlledLaneCount: 120,
    multiMovementLaneCount: 96,
    arrowsPerControlledLane: 1,
    maximumLaneWidthRatio: LANE_ARROW_MAX_LANE_WIDTH_RATIO,
  },
  totals,
  baselineErrors,
  intersections,
  status: baselineErrors.length === 0 && intersections.every((item) => item.status === 'pass')
    ? 'pass'
    : 'fail',
}

await mkdir(dirname(output), { recursive: true })
await writeFile(output, `${JSON.stringify(report, null, 2)}\n`)
console.log(JSON.stringify({ output, status: report.status, totals }, null, 2))
if (report.status !== 'pass') process.exitCode = 1
