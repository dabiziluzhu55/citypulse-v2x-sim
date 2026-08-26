import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

import { extractGlbTriangles } from './focus-building-tileset.mjs'
import {
  edgeCenterline,
  edgeRoadWidth,
  visualLanePoints,
} from '../src/mapv/realistic/intersectionRoadGeometry.ts'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const publicDirectory = path.join(frontendDirectory, 'public')
const INTERSECTION_COUNT = 20
const CORE_RADIUS_METERS = 50
const FAR_FIELD_RADIUS_METERS = 200
const ROAD_SAMPLE_SPACING_METERS = 2
const FAR_FIELD_TARGETS = new Set(['demo_5', 'demo_6', 'demo_9'])
const JOINT_BUFFER_METERS = 12
const RANGE_PADDING_METERS = 1.5
const RANGE_MERGE_GAP_METERS = 2
const EPSILON = 1e-7

export const MANUAL_VISUAL_OVERRIDE_AREAS = {
  demo_5: [
    {
      id: 'marked_1',
      edges: {
        '-50399': [0, 15.574],
        '-56681': [115.594, 131.168],
      },
      jointId: '3975:1',
    },
    {
      id: 'marked_2',
      edges: {
        '-50347': [0, 26.364],
        '-56629': [16.839, 44.209],
      },
      jointId: '3947:1',
    },
    {
      id: 'marked_3',
      edges: {
        '-50399': [120.962, 131.167],
        '-56681': [0, 10.205],
      },
      jointId: '3976:1',
    },
    {
      id: 'marked_4',
      edges: {
        '-50348': [0, 15.917],
        '-56630': [107.147, 127.539],
      },
      jointId: '3948:1',
    },
    {
      id: 'marked_5',
      edges: {
        '-50400': [6.074, 28.588],
        '-56682': [47.809, 71.03],
      },
    },
    {
      id: 'marked_6',
      edges: {
        '-50349': [0, 17.861],
        '-56631': [284.093, 305.785],
      },
      jointId: '3805:1',
    },
  ],
}

const MANUAL_VISUAL_METADATA = {
  reason: 'building_overlap_visual_override',
  source: 'manual_visual_review',
}

function configuredManualExclusions(intersectionId) {
  const byEdge = new Map()
  for (const area of MANUAL_VISUAL_OVERRIDE_AREAS[intersectionId] ?? []) {
    for (const [edgeId, [startOffsetMeters, endOffsetMeters]] of Object.entries(area.edges)) {
      const ranges = byEdge.get(edgeId) ?? []
      ranges.push({
        startOffsetMeters,
        endOffsetMeters,
        ...MANUAL_VISUAL_METADATA,
      })
      byEdge.set(edgeId, ranges)
    }
  }
  return byEdge
}

function applyConfiguredJointOverrides(manifest, intersectionId) {
  const areas = MANUAL_VISUAL_OVERRIDE_AREAS[intersectionId]
  if (!areas) return false
  const hiddenJointIds = new Set(areas.flatMap((area) => area.jointId ? [area.jointId] : []))
  let changed = false
  for (const joint of manifest.roadJoints ?? []) {
    const next = hiddenJointIds.has(joint.jointId) ? MANUAL_VISUAL_METADATA : undefined
    if (JSON.stringify(joint.surfaceHidden) === JSON.stringify(next)) continue
    if (next) joint.surfaceHidden = { ...next }
    else delete joint.surfaceHidden
    changed = true
  }
  return changed
}

async function readJson(file) {
  return JSON.parse(await readFile(file, 'utf8'))
}

async function writeFileWithRetry(file, content, attempts = 5) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await writeFile(file, content)
      return
    } catch (cause) {
      const code = cause instanceof Error && 'code' in cause ? String(cause.code) : ''
      if (!['UNKNOWN', 'EBUSY', 'EPERM'].includes(code) || attempt === attempts) throw cause
      await delay(80 * attempt)
    }
  }
}

function applyMatrix4(matrix, [x, y]) {
  return [
    matrix[0] * x + matrix[4] * y + matrix[12],
    matrix[1] * x + matrix[5] * y + matrix[13],
  ]
}

function signedAreaTwice(points) {
  return points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length]
    return sum + point[0] * next[1] - next[0] * point[1]
  }, 0)
}

function orientation(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

function pointOnSegment(point, start, end) {
  return Math.abs(orientation(start, end, point)) <= EPSILON
    && point[0] >= Math.min(start[0], end[0]) - EPSILON
    && point[0] <= Math.max(start[0], end[0]) + EPSILON
    && point[1] >= Math.min(start[1], end[1]) - EPSILON
    && point[1] <= Math.max(start[1], end[1]) + EPSILON
}

function segmentsIntersect(a, b, c, d) {
  const abC = orientation(a, b, c)
  const abD = orientation(a, b, d)
  const cdA = orientation(c, d, a)
  const cdB = orientation(c, d, b)
  if (((abC > EPSILON && abD < -EPSILON) || (abC < -EPSILON && abD > EPSILON))
    && ((cdA > EPSILON && cdB < -EPSILON) || (cdA < -EPSILON && cdB > EPSILON))) return true
  return (Math.abs(abC) <= EPSILON && pointOnSegment(c, a, b))
    || (Math.abs(abD) <= EPSILON && pointOnSegment(d, a, b))
    || (Math.abs(cdA) <= EPSILON && pointOnSegment(a, c, d))
    || (Math.abs(cdB) <= EPSILON && pointOnSegment(b, c, d))
}

function pointInPolygon(point, polygon) {
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const start = polygon[previous]
    const end = polygon[index]
    if (pointOnSegment(point, start, end)) return true
    if ((start[1] > point[1]) === (end[1] > point[1])) continue
    const x = (end[0] - start[0]) * (point[1] - start[1]) / (end[1] - start[1]) + start[0]
    if (point[0] < x) inside = !inside
  }
  return inside
}

function polygonsIntersect(left, right) {
  if (left.some((point) => pointInPolygon(point, right))) return true
  if (right.some((point) => pointInPolygon(point, left))) return true
  for (let leftIndex = 0; leftIndex < left.length; leftIndex += 1) {
    const leftNext = (leftIndex + 1) % left.length
    for (let rightIndex = 0; rightIndex < right.length; rightIndex += 1) {
      const rightNext = (rightIndex + 1) % right.length
      if (segmentsIntersect(left[leftIndex], left[leftNext], right[rightIndex], right[rightNext])) return true
    }
  }
  return false
}

function pointSegmentDistance(point, start, end) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const squared = dx * dx + dy * dy
  const ratio = squared > EPSILON
    ? Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared))
    : 0
  return Math.hypot(point[0] - start[0] - dx * ratio, point[1] - start[1] - dy * ratio)
}

function polygonDistance(left, right) {
  if (polygonsIntersect(left, right)) return 0
  let minimum = Number.POSITIVE_INFINITY
  for (const point of left) {
    for (let index = 0; index < right.length; index += 1) {
      minimum = Math.min(minimum, pointSegmentDistance(point, right[index], right[(index + 1) % right.length]))
    }
  }
  for (const point of right) {
    for (let index = 0; index < left.length; index += 1) {
      minimum = Math.min(minimum, pointSegmentDistance(point, left[index], left[(index + 1) % left.length]))
    }
  }
  return minimum
}

function segmentStrip(start, end, width) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const length = Math.hypot(dx, dy) || 1
  const nx = -dy / length * width / 2
  const ny = dx / length * width / 2
  return [
    [start[0] + nx, start[1] + ny],
    [end[0] + nx, end[1] + ny],
    [end[0] - nx, end[1] - ny],
    [start[0] - nx, start[1] - ny],
  ]
}

function triangleIntersectsPolyline(triangle, points) {
  if (points.some((point) => pointInPolygon(point, triangle))) return true
  for (let index = 1; index < points.length; index += 1) {
    for (let side = 0; side < triangle.length; side += 1) {
      if (segmentsIntersect(points[index - 1], points[index], triangle[side], triangle[(side + 1) % triangle.length])) {
        return true
      }
    }
  }
  return false
}

function polylineLengths(points) {
  const cumulative = [0]
  for (let index = 1; index < points.length; index += 1) {
    cumulative.push(cumulative.at(-1) + Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1],
    ))
  }
  return { cumulative, total: cumulative.at(-1) ?? 0 }
}

function mergeRanges(
  ranges,
  totalMeters,
  reason = 'building_overlap',
  paddingMeters = RANGE_PADDING_METERS,
  mergeGapMeters = RANGE_MERGE_GAP_METERS,
) {
  const sorted = ranges
    .map(([start, end]) => [
      Math.max(0, start - paddingMeters),
      Math.min(totalMeters, end + paddingMeters),
    ])
    .filter(([start, end]) => end - start > 0.1)
    .sort((left, right) => left[0] - right[0])
  const merged = []
  for (const range of sorted) {
    const previous = merged.at(-1)
    if (!previous || range[0] > previous[1] + mergeGapMeters) merged.push([...range])
    else previous[1] = Math.max(previous[1], range[1])
  }
  return merged.map(([startOffsetMeters, endOffsetMeters]) => ({
    startOffsetMeters: Number(startOffsetMeters.toFixed(3)),
    endOffsetMeters: Number(endOffsetMeters.toFixed(3)),
    reason,
    source: 'building_triangle_audit',
  }))
}

function samplePolylineAtDistance(points, cumulative, distance) {
  const target = Math.max(0, Math.min(cumulative.at(-1) ?? 0, distance))
  for (let index = 1; index < points.length; index += 1) {
    if (target > cumulative[index] && index < points.length - 1) continue
    const length = cumulative[index] - cumulative[index - 1]
    const ratio = length > EPSILON ? (target - cumulative[index - 1]) / length : 0
    return [
      points[index - 1][0] + (points[index][0] - points[index - 1][0]) * ratio,
      points[index - 1][1] + (points[index][1] - points[index - 1][1]) * ratio,
    ]
  }
  return [...points.at(-1)]
}

function resamplePolyline(points, maximumSpacing) {
  const { cumulative, total } = polylineLengths(points)
  const segments = Math.max(1, Math.ceil(total / maximumSpacing))
  return Array.from({ length: segments + 1 }, (_, index) => (
    samplePolylineAtDistance(points, cumulative, total * index / segments)
  ))
}

function outsideCircleRanges(start, end, radius) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const aa = dx * dx + dy * dy
  if (aa <= EPSILON) return Math.hypot(...start) >= radius ? [[0, 1]] : []
  const bb = 2 * (start[0] * dx + start[1] * dy)
  const cc = start[0] * start[0] + start[1] * start[1] - radius * radius
  const discriminant = bb * bb - 4 * aa * cc
  const breakpoints = [0, 1]
  if (discriminant > 0) {
    const root = Math.sqrt(discriminant)
    for (const ratio of [(-bb - root) / (2 * aa), (-bb + root) / (2 * aa)]) {
      if (ratio > EPSILON && ratio < 1 - EPSILON) breakpoints.push(ratio)
    }
  }
  breakpoints.sort((left, right) => left - right)
  const ranges = []
  for (let index = 1; index < breakpoints.length; index += 1) {
    const from = breakpoints[index - 1]
    const to = breakpoints[index]
    const middle = (from + to) / 2
    if (Math.hypot(start[0] + dx * middle, start[1] + dy * middle) >= radius) {
      ranges.push([from, to])
    }
  }
  return ranges
}

function triangleBounds(points) {
  return {
    minX: Math.min(...points.map((point) => point[0])),
    maxX: Math.max(...points.map((point) => point[0])),
    minY: Math.min(...points.map((point) => point[1])),
    maxY: Math.max(...points.map((point) => point[1])),
  }
}

function boundsIntersect(left, right) {
  return left.maxX >= right.minX && left.minX <= right.maxX
    && left.maxY >= right.minY && left.minY <= right.maxY
}

async function loadBuildingTriangles(intersectionId, manifest) {
  const tilesDirectory = path.join(publicDirectory, '3dtiles', 'intersections', intersectionId)
  const tileset = await readJson(path.join(tilesDirectory, 'tileset.json'))
  const transform = tileset.root?.transform
  if (!Array.isArray(transform) || transform.length !== 16) {
    throw new Error(`${intersectionId} building tileset has no valid root transform`)
  }
  const origin = manifest.origin?.webMercator
  if (!Array.isArray(origin) || origin.length !== 2) {
    throw new Error(`${intersectionId} manifest has no WebMercator origin`)
  }
  const files = (await readdir(path.join(tilesDirectory, 'tiles')))
    .filter((name) => name.startsWith('tile_roof_') && name.endsWith('.glb'))
    .sort()
  const triangles = []
  for (const file of files) {
    const glb = await readFile(path.join(tilesDirectory, 'tiles', file))
    for (const triangle of extractGlbTriangles(glb)) {
      const points = triangle.map((point) => {
        const projected = applyMatrix4(transform, point)
        return [projected[0] - origin[0], projected[1] - origin[1]]
      })
      if (Math.abs(signedAreaTwice(points)) < 0.02) continue
      triangles.push({ points, bounds: triangleBounds(points), tile: file })
    }
  }
  return triangles
}

export function findRoadSurfaceExclusions(manifest, triangles, options = {}) {
  const horizontalScale = manifest.horizontalScale ?? 1
  const allowFarFieldCenterlineConflicts = options.allowFarFieldCenterlineConflicts === true
  const farFieldRadiusSceneUnits = (options.farFieldRadiusMeters ?? FAR_FIELD_RADIUS_METERS) * horizontalScale
  const protectedPolygons = [
    manifest.junctionShape,
    ...(manifest.roadJoints ?? []).flatMap((joint) => joint.surfaceParts
      ? Object.values(joint.surfaceParts).flatMap((parts) => parts.flatMap((part) => [part.outer, ...(part.holes ?? [])]))
      : Object.values(joint.polygons)),
  ].filter((polygon) => Array.isArray(polygon) && polygon.length >= 3)
  const edgeResults = []
  const centerlineConflicts = []

  for (const edge of manifest.edges) {
    if (edge.incident !== false) continue
    const centerline = resamplePolyline(
      edgeCenterline(edge),
      ROAD_SAMPLE_SPACING_METERS * horizontalScale,
    )
    if (centerline.length < 2) continue
    const { cumulative, total } = polylineLengths(centerline)
    const outerWidth = edgeRoadWidth(edge) + 6.36 * horizontalScale
    const ranges = []
    const conflictTiles = new Set()
    const laneCenterlineTriangles = new Set(triangles.filter((triangle) => edge.lanes.some((lane) => (
      triangleIntersectsPolyline(triangle.points, visualLanePoints(lane))
    ))))

    for (let index = 1; index < centerline.length; index += 1) {
      const start = centerline[index - 1]
      const end = centerline[index]
      const footprint = segmentStrip(start, end, outerWidth)
      const footprintBounds = triangleBounds(footprint)
      const nearJoint = protectedPolygons.some((polygon) => (
        polygonDistance(footprint, polygon) <= JOINT_BUFFER_METERS * horizontalScale
      ))
      const hits = triangles.filter((triangle) => (
        boundsIntersect(footprintBounds, triangle.bounds)
        && polygonsIntersect(footprint, triangle.points)
      ))
      if (hits.length === 0) continue
      const centerlineHits = hits.filter((triangle) => laneCenterlineTriangles.has(triangle))
      if (centerlineHits.length > 0) {
        centerlineHits.forEach((triangle) => conflictTiles.add(triangle.tile))
        centerlineConflicts.push({
          edgeId: edge.id,
          startOffsetMeters: Number((cumulative[index - 1] / horizontalScale).toFixed(3)),
          endOffsetMeters: Number((cumulative[index] / horizontalScale).toFixed(3)),
          tiles: [...new Set(centerlineHits.map((triangle) => triangle.tile))].sort(),
        })
        if (!allowFarFieldCenterlineConflicts || nearJoint) continue
      }
      if (nearJoint) continue
      const allowedRanges = allowFarFieldCenterlineConflicts
        ? outsideCircleRanges(start, end, farFieldRadiusSceneUnits)
        : Math.hypot((start[0] + end[0]) / 2, (start[1] + end[1]) / 2) > CORE_RADIUS_METERS * horizontalScale
          ? [[0, 1]]
          : []
      const segmentLength = cumulative[index] - cumulative[index - 1]
      ranges.push(...allowedRanges.map(([from, to]) => [
        (cumulative[index - 1] + segmentLength * from) / horizontalScale,
        (cumulative[index - 1] + segmentLength * to) / horizontalScale,
      ]))
      hits.forEach((triangle) => conflictTiles.add(triangle.tile))
    }

    const surfaceExclusions = mergeRanges(
      ranges,
      total / horizontalScale,
      allowFarFieldCenterlineConflicts ? 'building_overlap_far_field' : 'building_overlap',
      allowFarFieldCenterlineConflicts ? 0 : RANGE_PADDING_METERS,
      allowFarFieldCenterlineConflicts ? 0.01 : RANGE_MERGE_GAP_METERS,
    )
    if (surfaceExclusions.length > 0) {
      edgeResults.push({ edgeId: edge.id, surfaceExclusions, tiles: [...conflictTiles].sort() })
    }
  }
  return { edges: edgeResults, centerlineConflicts }
}

export async function auditRoadBuildingCollisions({ apply = false, targets } = {}) {
  const selectedTargets = targets?.length ? new Set(targets) : null
  const intersections = []
  for (let index = 1; index <= INTERSECTION_COUNT; index += 1) {
    const intersectionId = `demo_${index}`
    if (selectedTargets && !selectedTargets.has(intersectionId)) continue
    const manifestPath = path.join(publicDirectory, 'intersections', 'v3', intersectionId, 'manifest.json')
    const manifest = await readJson(manifestPath)
    const triangles = await loadBuildingTriangles(intersectionId, manifest)
    const result = findRoadSurfaceExclusions(manifest, triangles, {
      allowFarFieldCenterlineConflicts: FAR_FIELD_TARGETS.has(intersectionId),
      farFieldRadiusMeters: FAR_FIELD_RADIUS_METERS,
    })
    if (apply) {
      const byEdge = new Map(result.edges.map((edge) => [edge.edgeId, edge.surfaceExclusions]))
      const configuredManual = configuredManualExclusions(intersectionId)
      const hasConfiguredManualAreas = Object.hasOwn(MANUAL_VISUAL_OVERRIDE_AREAS, intersectionId)
      let changed = false
      for (const edge of manifest.edges) {
        const retainedManual = hasConfiguredManualAreas
          ? []
          : (edge.surfaceExclusions ?? []).filter((exclusion) => (
              exclusion.reason === MANUAL_VISUAL_METADATA.reason
              && exclusion.source === MANUAL_VISUAL_METADATA.source
            ))
        const next = [
          ...(byEdge.get(edge.id) ?? []),
          ...(configuredManual.get(edge.id) ?? retainedManual),
        ]
        if (next?.length) {
          if (JSON.stringify(edge.surfaceExclusions ?? []) !== JSON.stringify(next)) changed = true
          edge.surfaceExclusions = next
        } else if (edge.surfaceExclusions) {
          delete edge.surfaceExclusions
          changed = true
        }
      }
      if (applyConfiguredJointOverrides(manifest, intersectionId)) changed = true
      if (changed) await writeFileWithRetry(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
    }
    intersections.push({
      intersectionId,
      roofTriangles: triangles.length,
      exclusionEdges: result.edges,
      centerlineConflicts: result.centerlineConflicts,
    })
  }
  return {
    schemaVersion: 1,
    policy: {
      buildingGeometry: 'projected GLB roof triangles',
      protectedRoads: 'incident edges, lane centerlines, primary junction, and road joints with buffers',
      exclusion: 'non-core widened road surfaces, plus audited centerline conflicts beyond 200 m for demo_5/demo_6/demo_9',
      farFieldRadiusMeters: FAR_FIELD_RADIUS_METERS,
      maximumRoadSampleSpacingMeters: ROAD_SAMPLE_SPACING_METERS,
    },
    intersections,
    summary: {
      intersectionsWithExclusions: intersections
        .filter((item) => item.exclusionEdges.length > 0)
        .map((item) => item.intersectionId),
      exclusionEdges: intersections.reduce((sum, item) => sum + item.exclusionEdges.length, 0),
      exclusionRanges: intersections.reduce((sum, item) => (
        sum + item.exclusionEdges.reduce((edgeSum, edge) => edgeSum + edge.surfaceExclusions.length, 0)
      ), 0),
      centerlineConflicts: intersections.reduce((sum, item) => sum + item.centerlineConflicts.length, 0),
    },
  }
}

async function main() {
  const apply = process.argv.includes('--apply')
  const targetsArgument = process.argv.find((argument) => argument.startsWith('--targets='))
  const targets = targetsArgument
    ? targetsArgument.slice('--targets='.length).split(',').filter(Boolean)
    : undefined
  const report = await auditRoadBuildingCollisions({ apply, targets })
  console.table(report.intersections.map((item) => ({
    intersection: item.intersectionId,
    triangles: item.roofTriangles,
    edges: item.exclusionEdges.length,
    ranges: item.exclusionEdges.reduce((sum, edge) => sum + edge.surfaceExclusions.length, 0),
    centerline: item.centerlineConflicts.length,
  })))
  console.log(JSON.stringify(report.summary))
  const outputArgument = process.argv.find((argument) => argument.startsWith('--output='))
  if (outputArgument) {
    const output = path.resolve(frontendDirectory, outputArgument.slice('--output='.length))
    await mkdir(path.dirname(output), { recursive: true })
    await writeFile(output, `${JSON.stringify(report, null, 2)}\n`)
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  await main()
}
