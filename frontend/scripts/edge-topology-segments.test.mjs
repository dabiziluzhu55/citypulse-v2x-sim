// edge_id → 有向拓扑线段着色

import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(
  readFileSync(join(root, 'public/intersections/v3/edge-to-topology-segment.json'), 'utf8'),
)
const source = readFileSync(join(root, 'src/utils/edgeTopologySegments.ts'), 'utf8')

function normalizeCongestionLevel(value) {
  if (value === 'slow' || value === 'congested' || value === 'severe' || value === 'free') return value
  return 'free'
}
const RANK = { free: 0, slow: 1, congested: 2, severe: 3 }
function worse(a, b) {
  return RANK[a] >= RANK[b] ? a : b
}

function buildDirectedRouteCongestionLevels(trafficStyle, routeIds, edgeToSegment) {
  const levels = {}
  for (const routeId of routeIds) levels[routeId] = 'free'
  for (const [edgeId, style] of Object.entries(trafficStyle?.edges ?? {})) {
    if (edgeId.startsWith(':')) continue
    const segmentId = edgeToSegment[edgeId]
    if (!segmentId || !(segmentId in levels)) continue
    levels[segmentId] = worse(levels[segmentId], normalizeCongestionLevel(style.level))
  }
  return levels
}

test('manifest has directed segments and ignores internal edges', () => {
  assert.equal(manifest.schemaVersion, 1)
  assert.ok(Object.keys(manifest.edgeToSegment).length > 0)
  for (const edgeId of Object.keys(manifest.edgeToSegment)) {
    assert.equal(edgeId.startsWith(':'), false)
  }
  for (const segmentId of Object.keys(manifest.segments)) {
    assert.match(segmentId, /^demo_\d+:demo_\d+$/)
  }
})

test('opposite directions can receive different congestion colors', () => {
  const edgesBySegment = new Map()
  for (const [edgeId, segmentId] of Object.entries(manifest.edgeToSegment)) {
    const list = edgesBySegment.get(segmentId) ?? []
    list.push(edgeId)
    edgesBySegment.set(segmentId, list)
  }
  let forward = null
  let reverse = null
  for (const segmentId of edgesBySegment.keys()) {
    const [from, to] = segmentId.split(':')
    const candidate = `${to}:${from}`
    if (edgesBySegment.has(candidate)) {
      forward = segmentId
      reverse = candidate
      break
    }
  }
  assert.ok(forward)
  assert.ok(reverse)
  const forwardEdge = edgesBySegment.get(forward)[0]
  const reverseEdge = edgesBySegment.get(reverse)[0]
  assert.ok(forwardEdge)
  assert.ok(reverseEdge)
  const levels = buildDirectedRouteCongestionLevels(
    {
      edges: {
        [forwardEdge]: { level: 'severe' },
        [reverseEdge]: { level: 'slow' },
      },
    },
    [forward, reverse],
    manifest.edgeToSegment,
  )
  assert.equal(levels[forward], 'severe')
  assert.equal(levels[reverse], 'slow')
})

test('batches missing-edge diagnostics instead of logging once per edge', () => {
  assert.match(source, /pendingMissingEdges\.add\(edgeId\)/)
  assert.match(source, /queueMicrotask\(flushMissingEdgeWarning\)/)
  assert.doesNotMatch(source, /missing mapping for edge_id=/)
})
