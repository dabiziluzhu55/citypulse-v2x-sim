export const TARGET_TLS_ID = '317'
export const TARGET_INTERSECTION_ID = 'demo_2'
export const TARGET_EDGE_IDS = ['-56734', '-51425', '-57228', '-56736', '-57229', '-45801']
export const REBUILD_RADIUS_METERS = 140

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

function pointOnRadius(a, b, radius) {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const aa = dx * dx + dy * dy
  const bb = 2 * (a[0] * dx + a[1] * dy)
  const cc = a[0] * a[0] + a[1] * a[1] - radius * radius
  const discriminant = Math.max(0, bb * bb - 4 * aa * cc)
  const roots = [
    (-bb - Math.sqrt(discriminant)) / (2 * aa),
    (-bb + Math.sqrt(discriminant)) / (2 * aa),
  ]
  const t = roots.find((candidate) => candidate >= 0 && candidate <= 1) ?? 0
  return [a[0] + dx * t, a[1] + dy * t]
}

export function cropPolylineToRadius(points, radius) {
  if (points.length < 2) return points
  const result = []
  const inside = (point) => Math.hypot(point[0], point[1]) <= radius + 1e-6

  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index]
    const end = points[index + 1]
    const startInside = inside(start)
    const endInside = inside(end)
    if (startInside && result.length === 0) result.push(start)
    if (startInside !== endInside) result.push(pointOnRadius(start, end, radius))
    if (endInside) result.push(end)
  }
  return result.filter((point, index) => {
    if (index === 0) return true
    const previous = result[index - 1]
    return Math.hypot(point[0] - previous[0], point[1] - previous[1]) > 0.001
  })
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
      const sceneRadius = manifest.radiusSceneUnits ?? manifest.radiusMeters
      if (lane.points.some((point) => Math.hypot(point[0], point[1]) > sceneRadius + 2)) {
        errors.push(`lane ${lane.id} exceeds the rebuild radius`)
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
  return errors
}
