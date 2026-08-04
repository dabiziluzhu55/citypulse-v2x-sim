import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { extractOsmLandcover } from './generate-showcase-landcover.mjs'

const METERS_PER_DEGREE = 110_900
const LAMP_SPACING_METERS = 34
const LAMP_END_CLEARANCE_METERS = 18
const LAMP_ROADSIDE_CLEARANCE_METERS = 3.2
const LAMP_DEDUP_DISTANCE_METERS = 4
const LANDCOVER_RADIUS_METERS = 700

function normalize([x, y]) {
  const length = Math.hypot(x, y) || 1
  return [x / length, y / length]
}

function distance(left, right) {
  return Math.hypot(left[0] - right[0], left[1] - right[1])
}

function dot(left, right) {
  return left[0] * right[0] + left[1] * right[1]
}

function toWgs84(point, manifest) {
  const scale = manifest.horizontalScale || 1
  const east = point[0] / scale
  const north = point[1] / scale
  return [
    manifest.origin.longitude + east / (Math.cos(manifest.origin.latitude * Math.PI / 180) * METERS_PER_DEGREE),
    manifest.origin.latitude + north / METERS_PER_DEGREE,
    0,
  ]
}

function polylineLength(points) {
  return points.slice(1).reduce((sum, point, index) => sum + distance(points[index], point), 0)
}

function pointAtProgress(points, progress) {
  const total = polylineLength(points)
  const target = total * Math.max(0, Math.min(1, progress))
  let consumed = 0
  for (let index = 0; index < points.length - 1; index += 1) {
    const length = distance(points[index], points[index + 1])
    if (consumed + length < target && index < points.length - 2) {
      consumed += length
      continue
    }
    const ratio = length > 1e-6 ? (target - consumed) / length : 0
    return [
      points[index][0] + (points[index + 1][0] - points[index][0]) * ratio,
      points[index][1] + (points[index + 1][1] - points[index][1]) * ratio,
    ]
  }
  return [...points.at(-1)]
}

function samplePolyline(points, spacing, clearance, phase = 0) {
  const segments = points.slice(0, -1).map((start, index) => {
    const end = points[index + 1]
    const delta = [end[0] - start[0], end[1] - start[1]]
    return { start, direction: normalize(delta), length: Math.hypot(...delta) }
  }).filter((segment) => segment.length > 0.1)
  const total = segments.reduce((sum, segment) => sum + segment.length, 0)
  const samples = []
  for (let target = clearance + phase; target < total - clearance; target += spacing) {
    let consumed = 0
    for (const segment of segments) {
      if (consumed + segment.length >= target) {
        const amount = target - consumed
        samples.push({
          point: [
            segment.start[0] + segment.direction[0] * amount,
            segment.start[1] + segment.direction[1] * amount,
          ],
          direction: segment.direction,
        })
        break
      }
      consumed += segment.length
    }
  }
  return samples
}

function edgeCenterline(edge) {
  if (edge.centerline?.length >= 2) return edge.centerline
  const lanes = edge.lanes
  const count = Math.min(...lanes.map((lane) => (lane.renderPoints ?? lane.points).length))
  return Array.from({ length: count }, (_, index) => [
    lanes.reduce((sum, lane) => sum + (lane.renderPoints ?? lane.points)[index][0], 0) / lanes.length,
    lanes.reduce((sum, lane) => sum + (lane.renderPoints ?? lane.points)[index][1], 0) / lanes.length,
  ])
}

function edgeWidth(edge) {
  return edge.roadWidth ?? edge.lanes.reduce((sum, lane) => sum + lane.width, 0)
}

function oppositeEdge(left, right, scale) {
  const leftPoints = edgeCenterline(left)
  const rightPoints = edgeCenterline(right)
  const leftDirection = normalize([
    leftPoints.at(-1)[0] - leftPoints[0][0],
    leftPoints.at(-1)[1] - leftPoints[0][1],
  ])
  const rightDirection = normalize([
    rightPoints.at(-1)[0] - rightPoints[0][0],
    rightPoints.at(-1)[1] - rightPoints[0][1],
  ])
  return dot(leftDirection, rightDirection) < -0.82
    && distance(leftPoints[0], rightPoints.at(-1)) < 42 * scale
    && distance(leftPoints.at(-1), rightPoints[0]) < 42 * scale
}

function physicalCorridors(manifest) {
  const scale = manifest.horizontalScale || 1
  const used = new Set()
  const corridors = []
  for (const edge of manifest.edges) {
    if (used.has(edge.id)) continue
    used.add(edge.id)
    const companion = manifest.edges.find((candidate) => (
      !used.has(candidate.id) && oppositeEdge(edge, candidate, scale)
    ))
    if (companion) used.add(companion.id)
    const primary = edgeCenterline(edge)
    if (!companion) {
      corridors.push({
        id: edge.id,
        points: primary,
        halfWidth: edgeWidth(edge) / 2,
        incident: edge.incident !== false,
        incoming: edge.incoming,
      })
      continue
    }
    const secondary = edgeCenterline(companion)
    const count = Math.max(20, Math.min(72, Math.max(primary.length, secondary.length)))
    const points = Array.from({ length: count }, (_, index) => {
      const progress = index / (count - 1)
      const left = pointAtProgress(primary, progress)
      const right = pointAtProgress(secondary, 1 - progress)
      return [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2]
    })
    const middle = Math.floor(count / 2)
    const carriagewaySeparation = distance(
      pointAtProgress(primary, middle / (count - 1)),
      pointAtProgress(secondary, 1 - middle / (count - 1)),
    )
    corridors.push({
      id: `${edge.id}|${companion.id}`,
      points,
      halfWidth: carriagewaySeparation / 2 + Math.max(edgeWidth(edge), edgeWidth(companion)) / 2,
      incident: edge.incident !== false || companion.incident !== false,
      incoming: edge.incoming || companion.incoming,
    })
  }
  return corridors
}

function buildEnvironment(manifest) {
  const scale = manifest.horizontalScale || 1
  const corridors = physicalCorridors(manifest)
  const lamps = []
  const cameras = []
  const addLamp = (corridor, sample, side, index) => {
    const normal = [-sample.direction[1], sample.direction[0]]
    const offset = corridor.halfWidth + LAMP_ROADSIDE_CLEARANCE_METERS * scale
    const point = [
      sample.point[0] + normal[0] * offset * side,
      sample.point[1] + normal[1] * offset * side,
    ]
    if (lamps.some((lamp) => distance(lamp.localPosition, point) < LAMP_DEDUP_DISTANCE_METERS * scale)) return
    const inward = [-normal[0] * side, -normal[1] * side]
    lamps.push({
      id: `lamp:${corridor.id}:${side}:${index}`,
      position: toWgs84(point, manifest),
      heading: Math.atan2(inward[1], inward[0]),
      localPosition: point,
    })
  }

  for (const corridor of corridors) {
    const spacing = LAMP_SPACING_METERS * scale
    const clearance = LAMP_END_CLEARANCE_METERS * scale
    samplePolyline(corridor.points, spacing, clearance).forEach((sample, index) => (
      addLamp(corridor, sample, 1, index)
    ))
    samplePolyline(corridor.points, spacing, clearance, spacing / 2).forEach((sample, index) => (
      addLamp(corridor, sample, -1, index)
    ))
    if (corridor.incident && corridor.incoming) {
      const sample = samplePolyline(corridor.points, Number.POSITIVE_INFINITY, 16 * scale)[0]
      if (sample) {
        const normal = [-sample.direction[1], sample.direction[0]]
        const point = [
          sample.point[0] + normal[0] * (corridor.halfWidth + 2.4 * scale),
          sample.point[1] + normal[1] * (corridor.halfWidth + 2.4 * scale),
        ]
        cameras.push({
          id: `camera:${corridor.id}`,
          position: toWgs84(point, manifest),
          heading: Math.atan2(-normal[1], -normal[0]),
        })
      }
    }
  }

  return {
    facilities: {
      schemaVersion: 2,
      intersectionId: manifest.intersectionId,
      sourceGeneratedAt: new Date(0).toISOString(),
      lamps: lamps.map(({ localPosition: _localPosition, ...lamp }) => lamp),
      cameras,
      signals: [],
      arrows: [],
      phaseTemplates: {},
    },
    vegetation: {
      schemaVersion: 1,
      source: { model: 'disabled', license: 'not-applicable' },
      cellSizeMeters: 300,
      items: [],
    },
  }
}

function landcoverBounds(manifest) {
  const latitudeDelta = LANDCOVER_RADIUS_METERS / METERS_PER_DEGREE
  const longitudeDelta = latitudeDelta
    / Math.max(0.2, Math.cos(manifest.origin.latitude * Math.PI / 180))
  return {
    west: manifest.origin.longitude - longitudeDelta,
    south: manifest.origin.latitude - latitudeDelta,
    east: manifest.origin.longitude + longitudeDelta,
    north: manifest.origin.latitude + latitudeDelta,
  }
}

function intersectionLandcover(osmXml, manifest) {
  const extracted = extractOsmLandcover(osmXml, landcoverBounds(manifest))
  const enrich = (collection, kind) => ({
    ...collection,
    metadata: {
      ...collection.metadata,
      intersectionId: manifest.intersectionId,
      radiusMeters: LANDCOVER_RADIUS_METERS,
      fallback: 'Baidu vector base map',
      kind,
    },
  })
  return {
    green: enrich(extracted.green, 'green'),
    water: enrich(extracted.water, 'water'),
  }
}

async function writeFileWithRetry(url, content) {
  let lastError
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      await writeFile(url, content)
      return
    } catch (cause) {
      lastError = cause
      if (!['EBUSY', 'EPERM', 'UNKNOWN'].includes(cause?.code) || attempt === 5) throw cause
      await new Promise((resolve) => setTimeout(resolve, 80 * (attempt + 1)))
    }
  }
  throw lastError
}

const osmXml = await readFile(new URL('../../data/maps/osm/TotalMap.osm', import.meta.url), 'utf8')

for (let index = 1; index <= 20; index += 1) {
  const id = `demo_${index}`
  const directory = new URL(`../public/intersections/v3/${id}/`, import.meta.url)
  const manifest = JSON.parse(await readFile(new URL('manifest.json', directory), 'utf8'))
  const generated = buildEnvironment(manifest)
  const landcover = intersectionLandcover(osmXml, manifest)
  const environment = {
    schemaVersion: 1,
    intersectionId: id,
    facilitiesUrl: `/intersections/v3/${id}/facilities.json`,
    streetlight: {
      modelUrl: '/assets/roadside/streetlight.glb',
      heightMeters: 7.5,
      modelYawDegrees: 180,
    },
    buildingTilesetUrl: `/3dtiles/intersections/${id}/tileset.json`,
    geojson: {
      green: `/intersections/v3/${id}/green.geojson`,
      water: `/intersections/v3/${id}/water.geojson`,
    },
  }
  await mkdir(directory, { recursive: true })
  await Promise.all([
    writeFileWithRetry(new URL('environment.json', directory), `${JSON.stringify(environment, null, 2)}\n`),
    writeFileWithRetry(new URL('facilities.json', directory), `${JSON.stringify(generated.facilities, null, 2)}\n`),
    writeFileWithRetry(new URL('vegetation.json', directory), `${JSON.stringify(generated.vegetation, null, 2)}\n`),
    writeFileWithRetry(new URL('green.geojson', directory), `${JSON.stringify(landcover.green)}\n`),
    writeFileWithRetry(new URL('water.geojson', directory), `${JSON.stringify(landcover.water)}\n`),
  ])
}

console.log('Generated OSM landcover and model-based roadside facilities for demo_1 through demo_20')
