export const TARGET_TLS_ID = '317'
export const TARGET_INTERSECTION_ID = 'demo_2'
export const TARGET_EDGE_IDS = ['-56734', '-51425', '-57228', '-56736', '-57229', '-45801']
export const REBUILD_RADIUS_METERS = 520

export function parseShape(value) {
  if (typeof value !== 'string' || value.length === 0) return []
  return value.split(/\s+/).map((pair) => {
    const [x, y] = pair.split(',').map(Number)
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      throw new Error(`Invalid SUMO shape coordinate: ${pair}`)
    }
    return [x, y]
  })
}

function segmentRadiusIntersections(a, b, radius) {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const aa = dx * dx + dy * dy
  if (aa <= 1e-12) return []
  const bb = 2 * (a[0] * dx + a[1] * dy)
  const cc = a[0] * a[0] + a[1] * a[1] - radius * radius
  const discriminant = bb * bb - 4 * aa * cc
  if (discriminant <= 0) return []
  const root = Math.sqrt(discriminant)
  return [(-bb - root) / (2 * aa), (-bb + root) / (2 * aa)]
    .filter((candidate) => candidate > 1e-9 && candidate < 1 - 1e-9)
    .sort((left, right) => left - right)
}

export function cropPolylineToRadius(points, radius) {
  if (points.length < 2) return points
  const fragments = []
  let current = []
  const pointAt = (a, b, ratio) => [
    a[0] + (b[0] - a[0]) * ratio,
    a[1] + (b[1] - a[1]) * ratio,
  ]
  const append = (point) => {
    const previous = current.at(-1)
    if (!previous || Math.hypot(point[0] - previous[0], point[1] - previous[1]) > 0.001) {
      current.push(point)
    }
  }
  const flush = () => {
    if (current.length >= 2) fragments.push(current)
    current = []
  }

  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index]
    const end = points[index + 1]
    const breaks = [0, ...segmentRadiusIntersections(start, end, radius), 1]
    for (let part = 0; part < breaks.length - 1; part += 1) {
      const from = breaks[part]
      const to = breaks[part + 1]
      const middle = pointAt(start, end, (from + to) / 2)
      if (Math.hypot(middle[0], middle[1]) > radius + 1e-6) {
        flush()
        continue
      }
      append(pointAt(start, end, from))
      append(pointAt(start, end, to))
    }
  }
  flush()
  return fragments.sort((left, right) => right.length - left.length)[0] ?? []
}

export function toLocalShape(shape, origin) {
  return shape.map(([x, y]) => [
    Number((x - origin[0]).toFixed(3)),
    Number((y - origin[1]).toFixed(3)),
  ])
}

export function validateIntersectionManifest(manifest) {
  const errors = []
  if (![1, 2, 3].includes(manifest.schemaVersion)) errors.push('schemaVersion must be 1, 2, or 3')
  if (!manifest.intersectionId) errors.push('intersectionId is required')
  const tlsIds = manifest.tlsIds ?? (manifest.tlsId ? [manifest.tlsId] : [])
  if (tlsIds.length === 0) errors.push('at least one tlsId is required')
  if (!Number.isFinite(manifest.origin?.longitude) || !Number.isFinite(manifest.origin?.latitude)) {
    errors.push('finite WGS84 origin is required')
  }
  if (manifest.schemaVersion === 3) {
    if (manifest.renderCoordinateSystem !== 'LOCAL_BD09_WEB_MERCATOR_METERS, Z-up') {
      errors.push('v3 assets must use the local BD-09 WebMercator contract')
    }
    if (!Number.isFinite(manifest.horizontalScale) || manifest.horizontalScale < 1.1 || manifest.horizontalScale > 1.5) {
      errors.push('v3 horizontalScale is outside the expected mainland China range')
    }
    if (!Array.isArray(manifest.origin?.bd09) || !Array.isArray(manifest.origin?.webMercator)) {
      errors.push('v3 assets require BD-09 and WebMercator origins')
    }
  }
  if (!Array.isArray(manifest.junctionShape) || manifest.junctionShape.length < 3) {
    errors.push('junctionShape must be a renderable polygon')
  }
  if (!Array.isArray(manifest.edges) || manifest.edges.length < 2) errors.push('incident road edges are required')
  if (!Array.isArray(manifest.connections) || manifest.connections.length === 0) {
    errors.push('controlled connections are required')
  }
  for (const edge of manifest.edges ?? []) {
    if (!Array.isArray(edge.lanes) || edge.lanes.length === 0) errors.push(`edge ${edge.id} has no lanes`)
    for (const lane of edge.lanes ?? []) {
      if (lane.points.length < 2) errors.push(`lane ${lane.id} must contain a renderable shape`)
      if (lane.renderPoints && lane.renderPoints.length < 2) {
        errors.push(`lane ${lane.id} renderPoints must contain a renderable shape`)
      }
      if (lane.kind && !['driving', 'bicycle', 'pedestrian'].includes(lane.kind)) {
        errors.push(`lane ${lane.id} has unsupported kind ${lane.kind}`)
      }
      const sceneRadius = manifest.radiusSceneUnits ?? manifest.radiusMeters
      if (lane.points.some((point) => Math.hypot(point[0], point[1]) > sceneRadius + 2)) {
        errors.push(`lane ${lane.id} exceeds the rebuild radius`)
      }
    }
  }
  for (const joint of manifest.roadJoints ?? []) {
    if (!joint.jointId || !joint.junctionId) errors.push('road joint ids are required')
    if (!['continuation', 'junction'].includes(joint.kind)) {
      errors.push(`road joint ${joint.jointId} has unsupported kind ${joint.kind}`)
    }
    if (!Array.isArray(joint.connectedEdgeIds) || joint.connectedEdgeIds.length < 2) {
      errors.push(`road joint ${joint.jointId} must connect at least two edges`)
    }
    if (!Number.isFinite(joint.maxGapMeters) || joint.maxGapMeters < 0) {
      errors.push(`road joint ${joint.jointId} has invalid maxGapMeters`)
    }
    if (!Number.isFinite(joint.overlapMeters) || joint.overlapMeters < 0.5) {
      errors.push(`road joint ${joint.jointId} must overlap road caps by at least 0.5 m`)
    }
    for (const layer of ['sidewalk', 'curb', 'asphalt']) {
      const polygon = joint.polygons?.[layer]
      if (!Array.isArray(polygon) || polygon.length < 3) {
        errors.push(`road joint ${joint.jointId} has an invalid ${layer} polygon`)
      }
    }
  }
  for (const tlsId of tlsIds) {
    const links = (manifest.connections ?? []).filter((item) => item.tlsId === tlsId)
    const maxLink = Math.max(-1, ...links.map((item) => item.linkIndex))
    for (const [phase, templates] of Object.entries(manifest.phaseTemplates ?? {})) {
      for (const [stage, state] of Object.entries(templates[tlsId] ?? {})) {
        if (typeof state !== 'string' || state.length <= maxLink) {
          errors.push(`phase ${phase} ${tlsId}.${stage} does not cover linkIndex ${maxLink}`)
        }
      }
    }
  }
  for (const connection of manifest.connections ?? []) {
    if (!connection.viaLaneId) continue
    if (!Array.isArray(connection.viaPoints) || connection.viaPoints.length < 2) {
      errors.push(`connection ${connection.tlsId}:${connection.linkIndex} has no internal lane path`)
    }
    if (!Array.isArray(connection.renderPoints) || connection.renderPoints.length < 2) {
      errors.push(`connection ${connection.tlsId}:${connection.linkIndex} has no visual turn path`)
    }
    if (!Array.isArray(connection.viaSegments) || connection.viaSegments.length === 0) {
      errors.push(`connection ${connection.tlsId}:${connection.linkIndex} has no internal lane segments`)
      continue
    }
    if (connection.viaSegments[0]?.laneId !== connection.viaLaneId) {
      errors.push(`connection ${connection.tlsId}:${connection.linkIndex} first segment does not match viaLaneId`)
    }
    for (const segment of connection.viaSegments) {
      if (!segment.laneId || !Array.isArray(segment.points) || segment.points.length < 2) {
        errors.push(`connection ${connection.tlsId}:${connection.linkIndex} has an invalid source segment`)
      }
      if (!Array.isArray(segment.renderPoints) || segment.renderPoints.length < 2) {
        errors.push(`connection ${connection.tlsId}:${connection.linkIndex} has an invalid visual segment`)
      }
    }
  }
  return errors
}
