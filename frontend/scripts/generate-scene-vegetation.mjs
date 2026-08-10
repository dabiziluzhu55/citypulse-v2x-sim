import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const METERS_PER_DEGREE = 110_900
const CELL_SIZE_METERS = 300
const TREE_SPACING_METERS = 38
const END_CLEARANCE_METERS = 28
const ROAD_CLEARANCE_METERS = 3.2
const FACILITY_CLEARANCE_METERS = 3

const VARIANTS = {
  tree: ['Tree-01-1', 'Tree-01-2', 'Tree-02-1', 'Tree-02-2', 'Tree-03-1', 'Tree-03-2'],
  hedge: ['Hedge-01'],
  bush: ['Bush-01', 'Bush-02', 'Bush-03', 'Bush-04', 'Bush-05'],
  grass: ['Grass-01', 'Grass-02', 'Grass-03'],
  flowers: ['Flowers-01', 'Flowers-02', 'Flowers-03', 'Flowers-04'],
}

function hash(value) {
  let result = 2166136261
  for (const character of value) {
    result ^= character.charCodeAt(0)
    result = Math.imul(result, 16777619)
  }
  return result >>> 0
}

function normalize([x, y]) {
  const magnitude = Math.hypot(x, y) || 1
  return [x / magnitude, y / magnitude]
}

function toLocal([longitude, latitude], origin) {
  return [
    (longitude - origin[0]) * Math.cos(origin[1] * Math.PI / 180) * METERS_PER_DEGREE,
    (latitude - origin[1]) * METERS_PER_DEGREE,
  ]
}

function toWgs84([x, y], origin) {
  return [
    origin[0] + x / (Math.cos(origin[1] * Math.PI / 180) * METERS_PER_DEGREE),
    origin[1] + y / METERS_PER_DEGREE,
    0,
  ]
}

function distanceToSegment(point, start, end) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const denominator = dx * dx + dy * dy
  const amount = denominator === 0
    ? 0
    : Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator))
  return Math.hypot(point[0] - (start[0] + dx * amount), point[1] - (start[1] + dy * amount))
}

function sampleLine(points, spacing, endClearance) {
  const segments = points.slice(0, -1).map((start, index) => {
    const end = points[index + 1]
    const length = Math.hypot(end[0] - start[0], end[1] - start[1])
    return { start, direction: normalize([end[0] - start[0], end[1] - start[1]]), length }
  }).filter((segment) => segment.length > 0.2)
  const total = segments.reduce((sum, segment) => sum + segment.length, 0)
  const result = []
  for (let target = endClearance; target <= total - endClearance; target += spacing) {
    let consumed = 0
    for (const segment of segments) {
      if (consumed + segment.length >= target) {
        const offset = target - consumed
        result.push({
          point: [
            segment.start[0] + segment.direction[0] * offset,
            segment.start[1] + segment.direction[1] * offset,
          ],
          direction: segment.direction,
        })
        break
      }
      consumed += segment.length
    }
  }
  return result
}

function isRoadClear(point, roads) {
  return roads.every((road) => road.segments.every(([start, end]) => (
    distanceToSegment(point, start, end) >= road.width / 2 + ROAD_CLEARANCE_METERS
  )))
}

function isFacilityClear(point, facilities) {
  return facilities.every((facility) => Math.hypot(
    point[0] - facility[0],
    point[1] - facility[1],
  ) >= FACILITY_CLEARANCE_METERS)
}

function cellFor(point) {
  return `${Math.floor(point[0] / CELL_SIZE_METERS)}:${Math.floor(point[1] / CELL_SIZE_METERS)}`
}

function chooseVariant(kind, seed) {
  const variants = VARIANTS[kind]
  return variants[hash(seed) % variants.length]
}

function makeItem({ id, kind, point, direction, origin, scale }) {
  const seed = `${id}:${kind}`
  const cell = cellFor(point)
  const jitter = ((hash(`${seed}:heading`) % 1000) / 1000 - 0.5) * 0.26
  return {
    id: `${kind}-${id}`,
    kind,
    variant: chooseVariant(kind, `${cell}:${kind}`),
    position: toWgs84(point, origin),
    heading: Math.atan2(direction[1], direction[0]) - Math.PI / 2 + jitter,
    scale: Number((scale * (0.9 + (hash(`${seed}:scale`) % 210) / 1000)).toFixed(3)),
    cell,
  }
}

function rectangle(center, direction, length, width) {
  const forward = normalize(direction)
  const across = [-forward[1], forward[0]]
  const halfLength = length / 2
  const halfWidth = width / 2
  return [
    [center[0] + forward[0] * halfLength + across[0] * halfWidth,
      center[1] + forward[1] * halfLength + across[1] * halfWidth],
    [center[0] - forward[0] * halfLength + across[0] * halfWidth,
      center[1] - forward[1] * halfLength + across[1] * halfWidth],
    [center[0] - forward[0] * halfLength - across[0] * halfWidth,
      center[1] - forward[1] * halfLength - across[1] * halfWidth],
    [center[0] + forward[0] * halfLength - across[0] * halfWidth,
      center[1] + forward[1] * halfLength - across[1] * halfWidth],
  ]
}

function parseRoads(roadGeoJson, origin) {
  return roadGeoJson.features
    .filter((feature) => feature.geometry?.type === 'LineString')
    .map((feature) => {
      const points = feature.geometry.coordinates.map((point) => toLocal(point, origin))
      const width = Math.max(2.8, Number(feature.properties?.width_m)
        || Math.max(1, Number(feature.properties?.lane_count) || 1) * 3.35)
      return {
        id: String(feature.properties?.edge_id ?? feature.id ?? 'road'),
        width,
        points,
        segments: points.slice(0, -1).map((point, index) => [point, points[index + 1]]),
      }
    })
}

export function buildVegetationManifest(roadGeoJson, facilityManifest = null) {
  const center = roadGeoJson.metadata?.center
  const origin = [Number(center?.longitude), Number(center?.latitude)]
  if (!origin.every(Number.isFinite)) throw new Error('Road GeoJSON requires a finite metadata.center')
  const roads = parseRoads(roadGeoJson, origin)
  const facilityPoints = facilityManifest
    ? ['lamps', 'signals', 'cameras'].flatMap((kind) => facilityManifest[kind] ?? [])
      .map((facility) => toLocal(facility.position, origin))
    : []
  const items = []

  for (const road of roads) {
    const samples = sampleLine(road.points, TREE_SPACING_METERS, END_CLEARANCE_METERS)
    samples.forEach((sample, index) => {
      const right = [sample.direction[1], -sample.direction[0]]
      const treePoint = [
        sample.point[0] + right[0] * (road.width / 2 + 6.4),
        sample.point[1] + right[1] * (road.width / 2 + 6.4),
      ]
      if (isRoadClear(treePoint, roads) && isFacilityClear(treePoint, facilityPoints)) {
        items.push(makeItem({
          id: `${road.id}-${index}`,
          kind: 'tree',
          point: treePoint,
          direction: sample.direction,
          origin,
          scale: 1.32,
        }))
      }

      const underPoint = [
        sample.point[0] + sample.direction[0] * TREE_SPACING_METERS * 0.46
          + right[0] * (road.width / 2 + 5.2),
        sample.point[1] + sample.direction[1] * TREE_SPACING_METERS * 0.46
          + right[1] * (road.width / 2 + 5.2),
      ]
      if (!isRoadClear(underPoint, roads) || !isFacilityClear(underPoint, facilityPoints)) return
      const selector = hash(`${road.id}:${index}:understory`) % 10
      const kind = selector < 5 ? 'bush' : selector < 7 ? 'hedge' : selector < 9 ? 'flowers' : 'grass'
      items.push(makeItem({
        id: `${road.id}-${index}`,
        kind,
        point: underPoint,
        direction: sample.direction,
        origin,
        scale: kind === 'hedge' ? 1.05 : kind === 'bush' ? 1.12 : 0.92,
      }))
    })
  }

  items.sort((a, b) => a.cell.localeCompare(b.cell) || a.id.localeCompare(b.id))
  return {
    schemaVersion: 1,
    source: {
      model: 'shapespark-low-poly-plants-kit.gltf',
      license: 'unverified',
    },
    cellSizeMeters: CELL_SIZE_METERS,
    items,
  }
}

export function buildVegetationGround(manifest, roadGeoJson, baseGreen = null) {
  const center = roadGeoJson.metadata?.center
  const origin = [Number(center?.longitude), Number(center?.latitude)]
  const roads = parseRoads(roadGeoJson, origin)
  const features = [...(baseGreen?.features ?? [])]
  for (const item of manifest.items.filter((candidate) => candidate.kind === 'tree')) {
    const point = toLocal(item.position, origin)
    const roadDirection = [Math.cos(item.heading + Math.PI / 2), Math.sin(item.heading + Math.PI / 2)]
    const corners = rectangle(point, roadDirection, 20, 5.2)
    if (!corners.every((corner) => isRoadClear(corner, roads))) continue
    const ring = [...corners, corners[0]].map((corner) => toWgs84(corner, origin).slice(0, 2))
    features.push({
      type: 'Feature',
      id: `vegetation-ground/${item.id}`,
      properties: { class: 'landscaped-roadside', vegetation_cell: item.cell },
      geometry: { type: 'Polygon', coordinates: [ring] },
    })
  }
  return { type: 'FeatureCollection', features }
}

async function main() {
  const roadsUrl = new URL('../public/showcase-data/demo_2.roads.wgs84.geojson', import.meta.url)
  const facilitiesUrl = new URL('../public/showcase-data/demo_2.facilities.json', import.meta.url)
  const outputUrl = new URL('../public/showcase-data/demo_2.vegetation.json', import.meta.url)
  const baseGreenUrl = new URL('../public/showcase-data/demo_2.green.geojson', import.meta.url)
  const groundUrl = new URL('../public/showcase-data/demo_2.vegetation-ground.geojson', import.meta.url)
  const [roads, facilities, baseGreen] = await Promise.all([
    readFile(roadsUrl, 'utf8').then(JSON.parse),
    readFile(facilitiesUrl, 'utf8').then(JSON.parse).catch(() => null),
    readFile(baseGreenUrl, 'utf8').then(JSON.parse).catch(() => null),
  ])
  const manifest = buildVegetationManifest(roads, facilities)
  const ground = buildVegetationGround(manifest, roads, baseGreen)
  await Promise.all([
    writeFile(outputUrl, `${JSON.stringify(manifest, null, 2)}\n`),
    writeFile(groundUrl, `${JSON.stringify(ground, null, 2)}\n`),
  ])
  const counts = Object.groupBy(manifest.items, (item) => item.kind)
  console.log(`Generated ${manifest.items.length} vegetation instances in ${new Set(manifest.items.map((item) => item.cell)).size} cells`)
  console.log(Object.fromEntries(Object.entries(counts).map(([kind, items]) => [kind, items.length])))
  console.log(`Generated ${ground.features.length - (baseGreen?.features.length ?? 0)} roadside ground patches`)
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fileURLToPath(new URL(`file:///${process.argv[1].replace(/\\/g, '/')}`))) {
  await main()
}
