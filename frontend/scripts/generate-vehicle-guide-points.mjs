import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const manifestDirectory = path.join(frontendDirectory, 'public/intersections/v3')
const MAX_LOCAL_LENGTH_ERROR_RATIO = 0.02
const MAX_ENDPOINT_ERROR_METERS = 0.15

function distance(left, right) {
  return Math.hypot(right[0] - left[0], right[1] - left[1])
}

function cumulativeDistances(points) {
  const values = [0]
  for (let index = 1; index < points.length; index += 1) {
    values.push(values.at(-1) + distance(points[index - 1], points[index]))
  }
  return values
}

function polylineLength(points) {
  return cumulativeDistances(points).at(-1) ?? 0
}

function sampleAtDistance(points, targetDistance) {
  if (points.length < 2) return points[0] ?? [0, 0]
  const cumulative = cumulativeDistances(points)
  const target = Math.max(0, Math.min(cumulative.at(-1), targetDistance))
  for (let index = 1; index < points.length; index += 1) {
    if (target > cumulative[index] && index < points.length - 1) continue
    const span = cumulative[index] - cumulative[index - 1]
    const ratio = span > 1e-9 ? (target - cumulative[index - 1]) / span : 0
    return [
      points[index - 1][0] + (points[index][0] - points[index - 1][0]) * ratio,
      points[index - 1][1] + (points[index][1] - points[index - 1][1]) * ratio,
    ]
  }
  return [...points.at(-1)]
}

function nearestDistance(point, points) {
  if (points.length < 2) return 0
  const cumulative = cumulativeDistances(points)
  let bestDistance = Number.POSITIVE_INFINITY
  let bestStation = 0
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1]
    const end = points[index]
    const dx = end[0] - start[0]
    const dy = end[1] - start[1]
    const lengthSquared = dx * dx + dy * dy
    const ratio = lengthSquared > 1e-9
      ? Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared))
      : 0
    const projected = [start[0] + dx * ratio, start[1] + dy * ratio]
    const candidateDistance = distance(point, projected)
    if (candidateDistance >= bestDistance) continue
    bestDistance = candidateDistance
    bestStation = cumulative[index - 1] + Math.sqrt(lengthSquared) * ratio
  }
  return bestStation
}

function nearestPointDistance(point, points) {
  const station = nearestDistance(point, points)
  return distance(point, sampleAtDistance(points, station))
}

function overlappingSlices(sourcePoints, renderPoints) {
  const sourceLength = polylineLength(sourcePoints)
  const renderLength = polylineLength(renderPoints)
  const sourceStartDistance = nearestDistance(renderPoints[0], sourcePoints)
  const sourceEndDistance = nearestDistance(renderPoints.at(-1), sourcePoints)
  const sourceFromRender = slicePolyline(
    sourcePoints,
    sourceStartDistance,
    sourceEndDistance,
  )
  const renderFromSource = slicePolyline(
    renderPoints,
    nearestDistance(sourcePoints[0], renderPoints),
    nearestDistance(sourcePoints.at(-1), renderPoints),
  )
  const sourceProjectionLength = polylineLength(sourceFromRender)
  const renderProjectionLength = polylineLength(renderFromSource)
  const renderBoundedError = Math.abs(sourceProjectionLength - renderLength)
  const sourceBoundedError = Math.abs(sourceLength - renderProjectionLength)
  return renderBoundedError <= sourceBoundedError
    ? {
        sourcePoints: sourceFromRender,
        renderPoints,
        sourceStartDistance,
        sourceEndDistance,
      }
    : {
        sourcePoints,
        renderPoints: renderFromSource,
        sourceStartDistance: 0,
        sourceEndDistance: sourceLength,
      }
}

function slicePolyline(points, firstDistance, secondDistance) {
  if (points.length < 2) return points
  const cumulative = cumulativeDistances(points)
  const start = Math.max(0, Math.min(firstDistance, secondDistance))
  const end = Math.min(cumulative.at(-1), Math.max(firstDistance, secondDistance))
  const sliced = [sampleAtDistance(points, start)]
  for (let index = 1; index < points.length - 1; index += 1) {
    if (cumulative[index] > start + 1e-6 && cumulative[index] < end - 1e-6) {
      sliced.push([...points[index]])
    }
  }
  sliced.push(sampleAtDistance(points, end))
  return firstDistance <= secondDistance ? sliced : sliced.reverse()
}

function splitGuideBySourceSegments(guide, sourceSegments) {
  const segmentLengths = sourceSegments.map((segment) => polylineLength(segment.points))
  const totalSourceLength = segmentLengths.reduce((sum, value) => sum + value, 0)
  if (guide.length < 2 || totalSourceLength <= 1e-9) return sourceSegments.map(() => [])
  const guideLength = polylineLength(guide)
  let sourceCursor = 0
  return segmentLengths.map((length) => {
    const start = guideLength * sourceCursor / totalSourceLength
    sourceCursor += length
    const end = guideLength * sourceCursor / totalSourceLength
    return slicePolyline(guide, start, end)
  })
}

function alignEndpoints(points, start, end) {
  const sourceLength = polylineLength(points)
  if (points.length < 2 || sourceLength <= 1e-9) return [start, end]
  const startDelta = [start[0] - points[0][0], start[1] - points[0][1]]
  const endDelta = [end[0] - points.at(-1)[0], end[1] - points.at(-1)[1]]
  const stations = cumulativeDistances(points)
  return points.map((point, index) => {
    const ratio = stations[index] / sourceLength
    return [
      point[0] + startDelta[0] * (1 - ratio) + endDelta[0] * ratio,
      point[1] + startDelta[1] * (1 - ratio) + endDelta[1] * ratio,
    ]
  })
}

function joinPolylines(polylines) {
  const joined = []
  for (const points of polylines) {
    for (const point of points) {
      const previous = joined.at(-1)
      if (previous && distance(previous, point) <= 0.001) continue
      joined.push([...point])
    }
  }
  return joined
}

function endpointTangentGuide(sourcePoints, start, end, startTangent, endTangent) {
  const sourceLength = polylineLength(sourcePoints)
  const chord = distance(start, end)
  const targetLength = Math.max(sourceLength, chord)
  const controlBase = Math.min(targetLength * 0.42, Math.max(0.5, chord * 0.35))
  const curveForControl = (controlLength) => {
    const firstControl = [
      start[0] + startTangent[0] * controlLength,
      start[1] + startTangent[1] * controlLength,
    ]
    const secondControl = [
      end[0] - endTangent[0] * controlLength,
      end[1] - endTangent[1] * controlLength,
    ]
    const parameters = [
      0,
      0.0000001,
      ...Array.from({ length: 63 }, (_, index) => (index + 1) / 64),
      0.9999999,
      1,
    ]
    return parameters.map((t) => {
      const inverse = 1 - t
      return [
        inverse ** 3 * start[0]
          + 3 * inverse ** 2 * t * firstControl[0]
          + 3 * inverse * t ** 2 * secondControl[0]
          + t ** 3 * end[0],
        inverse ** 3 * start[1]
          + 3 * inverse ** 2 * t * firstControl[1]
          + 3 * inverse * t ** 2 * secondControl[1]
          + t ** 3 * end[1],
      ]
    })
  }
  let low = 0
  let high = controlBase
  while (polylineLength(curveForControl(high)) < targetLength && high < targetLength * 8) high *= 1.5
  for (let iteration = 0; iteration < 48; iteration += 1) {
    const middle = (low + high) / 2
    if (polylineLength(curveForControl(middle)) < targetLength) low = middle
    else high = middle
  }
  // A zero handle degenerates the endpoint derivative into the chord direction.
  // Keep a millimetre-scale handle so even nearly straight, length-tight SUMO
  // connections retain the adjacent lane's authoritative tangent.
  return curveForControl(Math.max((low + high) / 2, 0.001))
}

function endpointTangent(points, end = false) {
  const first = end ? points.at(-2) : points[0]
  const second = end ? points.at(-1) : points[1]
  const magnitude = distance(first, second) || 1
  return [(second[0] - first[0]) / magnitude, (second[1] - first[1]) / magnitude]
}

function scaleNormalOffsets(points, start, end, factor) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const chord = Math.hypot(dx, dy)
  if (chord <= 1e-9) return points.map((point) => [...point])
  const tangent = [dx / chord, dy / chord]
  const normal = [-tangent[1], tangent[0]]
  return points.map((point) => {
    const relative = [point[0] - start[0], point[1] - start[1]]
    const along = relative[0] * tangent[0] + relative[1] * tangent[1]
    const lateral = (relative[0] * normal[0] + relative[1] * normal[1]) * factor
    return [
      start[0] + tangent[0] * along + normal[0] * lateral,
      start[1] + tangent[1] * along + normal[1] * lateral,
    ]
  })
}

function distancePreservingGuide(sourcePoints, renderPoints) {
  if (sourcePoints.length < 2 || renderPoints.length < 2) return null
  const start = renderPoints[0]
  const end = renderPoints.at(-1)
  const sourceLength = polylineLength(sourcePoints)
  const minimumLength = distance(start, end)
  const targetLength = Math.max(sourceLength, minimumLength)
  const aligned = alignEndpoints(sourcePoints, start, end)
  const alignedLength = polylineLength(aligned)
  if (Math.abs(alignedLength - targetLength) <= 1e-6) return aligned

  let low
  let high
  if (alignedLength > targetLength) {
    low = 0
    high = 1
  } else {
    low = 1
    high = 2
    while (polylineLength(scaleNormalOffsets(aligned, start, end, high)) < targetLength && high < 256) {
      high *= 2
    }
  }
  for (let iteration = 0; iteration < 48; iteration += 1) {
    const middle = (low + high) / 2
    if (polylineLength(scaleNormalOffsets(aligned, start, end, middle)) < targetLength) low = middle
    else high = middle
  }
  return scaleNormalOffsets(aligned, start, end, (low + high) / 2)
}

function sourceStationsMeters(sourcePoints, horizontalScale) {
  return cumulativeDistances(sourcePoints).map((value) => value / horizontalScale)
}

function guideStationsForSourceLength(guidePoints, sourceLengthMeters, horizontalScale) {
  const cumulative = cumulativeDistances(guidePoints)
  const guideLength = cumulative.at(-1) ?? 0
  return cumulative.map((value) => (
    guideLength > 1e-9 ? sourceLengthMeters * value / guideLength : value / horizontalScale
  ))
}

function writeGuide(
  target,
  sourcePoints,
  renderPoints,
  horizontalScale,
  sourceStartDistance = 0,
  sourceEndDistance = polylineLength(sourcePoints),
) {
  const guide = distancePreservingGuide(sourcePoints, renderPoints)
  if (!guide) return false
  target.vehicleGuidePoints = guide
  target.vehicleGuideSourceStationsMeters = sourceStationsMeters(sourcePoints, horizontalScale)
  target.vehicleGuideSourceStartMeters = Math.min(sourceStartDistance, sourceEndDistance) / horizontalScale
  target.vehicleGuideSourceEndMeters = Math.max(sourceStartDistance, sourceEndDistance) / horizontalScale
  return true
}

let laneCount = 0
let connectionSegmentCount = 0
let maximumLengthErrorRatio = 0
let maximumEndpointErrorMeters = 0
let endpointLimitedConnectionCount = 0
let maximumEndpointLimitedGapMeters = 0

for (let index = 1; index <= 20; index += 1) {
  const manifestPath = path.join(manifestDirectory, `demo_${index}`, 'manifest.json')
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const horizontalScale = Number(manifest.horizontalScale) || 1

  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      if (lane.points?.length < 2 || lane.renderPoints?.length < 2) continue
      const overlap = overlappingSlices(lane.points, lane.renderPoints)
      if (writeGuide(
        lane,
        overlap.sourcePoints,
        overlap.renderPoints,
        horizontalScale,
        overlap.sourceStartDistance,
        overlap.sourceEndDistance,
      )) {
        laneCount += 1
      }
    }
  }

  const edgeById = new Map(manifest.edges.map((edge) => [edge.id, edge]))
  for (const connection of manifest.vehicleConnections ?? []) {
    const segments = connection.viaSegments ?? []
    const fromLane = edgeById.get(connection.fromEdge)?.lanes
      .find((lane) => lane.index === connection.fromLane)
    const toLane = edgeById.get(connection.toEdge)?.lanes
      .find((lane) => lane.index === connection.toLane)
    if (
      connection.viaPoints?.length >= 2
      && fromLane?.vehicleGuidePoints?.length >= 2
      && toLane?.vehicleGuidePoints?.length >= 2
    ) {
      const connectionEndpoints = [
        fromLane.vehicleGuidePoints.at(-1),
        toLane.vehicleGuidePoints[0],
      ]
      const connectionSourcePoints = joinPolylines([
        [fromLane.points.at(-1)],
        connection.viaPoints,
        [toLane.points[0]],
      ])
      connection.vehicleSourcePoints = connectionSourcePoints
      connection.vehicleGuidePoints = endpointTangentGuide(
        connectionSourcePoints,
        connectionEndpoints[0],
        connectionEndpoints[1],
        endpointTangent(fromLane.vehicleGuidePoints, true),
        endpointTangent(toLane.vehicleGuidePoints),
      )
      connection.vehicleGuideSourceStationsMeters = guideStationsForSourceLength(
        connection.vehicleGuidePoints,
        polylineLength(connectionSourcePoints) / horizontalScale,
        horizontalScale,
      )
      if (connection.vehicleGuidePoints.length >= 2) {
        const sourceLength = polylineLength(connectionSourcePoints) / horizontalScale
        const endpointDistance = distance(...connectionEndpoints) / horizontalScale
        if (endpointDistance > sourceLength * (1 + MAX_LOCAL_LENGTH_ERROR_RATIO)) {
          connection.vehicleGuideEndpointLimited = true
          endpointLimitedConnectionCount += 1
          maximumEndpointLimitedGapMeters = Math.max(
            maximumEndpointLimitedGapMeters,
            endpointDistance - sourceLength,
          )
        } else {
          delete connection.vehicleGuideEndpointLimited
        }
        // An internal lane's own first and last SUMO positions are the authoritative
        // transition boundaries. Visual connector gaps must not add phantom travel.
        const connectionSourceSegments = segments.map((segment) => ({
          points: segment.points,
        }))
        const splitGuides = splitGuideBySourceSegments(
          connection.vehicleGuidePoints,
          connectionSourceSegments,
        )
        for (const [segmentIndex, segment] of segments.entries()) {
          segment.vehicleGuidePoints = splitGuides[segmentIndex]
          segment.vehicleSourcePoints = connectionSourceSegments[segmentIndex].points
          segment.vehicleGuideSourceStationsMeters = guideStationsForSourceLength(
            splitGuides[segmentIndex],
            polylineLength(connectionSourceSegments[segmentIndex].points) / horizontalScale,
            horizontalScale,
          )
          connectionSegmentCount += 1
        }
      }
    }
  }

  for (const target of [
    ...manifest.edges.flatMap((edge) => edge.lanes),
    ...(manifest.vehicleConnections ?? []),
  ]) {
    if (!target.vehicleGuidePoints?.length || !target.vehicleGuideSourceStationsMeters?.length) continue
    const sourceLength = target.vehicleGuideSourceStationsMeters.at(-1)
    const guideLength = polylineLength(target.vehicleGuidePoints) / horizontalScale
    const ratio = sourceLength > 1e-9 ? Math.abs(guideLength / sourceLength - 1) : 0
    const endpointLimited = target.vehicleGuideEndpointLimited === true
    if (endpointLimited) continue
    maximumLengthErrorRatio = Math.max(maximumLengthErrorRatio, ratio)
    maximumEndpointErrorMeters = Math.max(
      maximumEndpointErrorMeters,
      nearestPointDistance(target.vehicleGuidePoints[0], target.renderPoints) / horizontalScale,
      nearestPointDistance(target.vehicleGuidePoints.at(-1), target.renderPoints) / horizontalScale,
    )
  }
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
}

if (maximumLengthErrorRatio > MAX_LOCAL_LENGTH_ERROR_RATIO + 1e-9) {
  throw new Error(`Vehicle guide local length error ${(maximumLengthErrorRatio * 100).toFixed(2)}% exceeds 2%`)
}
if (maximumEndpointErrorMeters > MAX_ENDPOINT_ERROR_METERS + 1e-9) {
  throw new Error(`Vehicle guide endpoint error ${maximumEndpointErrorMeters.toFixed(3)}m exceeds 0.15m`)
}

console.log(
  `Wrote ${laneCount} lane and ${connectionSegmentCount} connection-segment vehicle guides; `
  + `maximum length error ${(maximumLengthErrorRatio * 100).toFixed(2)}%; `
  + `${endpointLimitedConnectionCount} endpoint-limited connections `
  + `(maximum unavoidable gap ${maximumEndpointLimitedGapMeters.toFixed(3)}m)`,
)
