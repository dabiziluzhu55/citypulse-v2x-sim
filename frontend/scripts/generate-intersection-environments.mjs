import { mkdir, readFile, writeFile } from 'node:fs/promises'

const METERS_PER_DEGREE = 110_900
const PLANT_VARIANTS = ['Tree-01-1', 'Tree-01-2', 'Tree-02-1', 'Tree-02-2', 'Tree-03-1', 'Tree-03-2']

function hash(value) {
  let result = 2166136261
  for (const character of value) {
    result ^= character.charCodeAt(0)
    result = Math.imul(result, 16777619)
  }
  return result >>> 0
}

function normalize([x, y]) {
  const length = Math.hypot(x, y) || 1
  return [x / length, y / length]
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

function samplePolyline(points, spacing, clearance) {
  const segments = points.slice(0, -1).map((start, index) => {
    const end = points[index + 1]
    const delta = [end[0] - start[0], end[1] - start[1]]
    return { start, direction: normalize(delta), length: Math.hypot(...delta) }
  }).filter((segment) => segment.length > 0.1)
  const total = segments.reduce((sum, segment) => sum + segment.length, 0)
  const samples = []
  for (let distance = clearance; distance < total - clearance; distance += spacing) {
    let consumed = 0
    for (const segment of segments) {
      if (consumed + segment.length >= distance) {
        const amount = distance - consumed
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

function centerline(edge) {
  const lanes = edge.lanes
  const count = Math.min(...lanes.map((lane) => lane.points.length))
  return Array.from({ length: count }, (_, index) => [
    lanes.reduce((sum, lane) => sum + lane.points[index][0], 0) / lanes.length,
    lanes.reduce((sum, lane) => sum + lane.points[index][1], 0) / lanes.length,
  ])
}

function buildEnvironment(manifest) {
  const lamps = []
  const cameras = []
  const vegetation = []
  for (const edge of manifest.edges) {
    const points = centerline(edge)
    const roadHalfWidth = edge.lanes.reduce((sum, lane) => sum + lane.width, 0) / 2
    const samples = samplePolyline(points, 54 * manifest.horizontalScale, 22 * manifest.horizontalScale)
    samples.forEach((sample, index) => {
      const normal = [-sample.direction[1], sample.direction[0]]
      const side = hash(`${manifest.intersectionId}:${edge.id}:${index}`) % 2 === 0 ? 1 : -1
      const lampOffset = roadHalfWidth + 2.2 * manifest.horizontalScale
      const treeOffset = roadHalfWidth + 6.5 * manifest.horizontalScale
      const lampPoint = [
        sample.point[0] + normal[0] * lampOffset * side,
        sample.point[1] + normal[1] * lampOffset * side,
      ]
      const treePoint = [
        sample.point[0] + normal[0] * treeOffset * side,
        sample.point[1] + normal[1] * treeOffset * side,
      ]
      lamps.push({
        id: `lamp:${edge.id}:${index}`,
        position: toWgs84(lampPoint, manifest),
        heading: Math.atan2(sample.direction[1], sample.direction[0]) - Math.PI / 2,
      })
      if (index === 0 && edge.incoming) cameras.push({
        id: `camera:${edge.id}`,
        position: toWgs84(lampPoint, manifest),
        heading: Math.atan2(sample.direction[1], sample.direction[0]) - Math.PI / 2,
      })
      vegetation.push({
        id: `tree:${edge.id}:${index}`,
        kind: 'tree',
        variant: PLANT_VARIANTS[hash(`${edge.id}:${index}`) % PLANT_VARIANTS.length],
        position: toWgs84(treePoint, manifest),
        heading: (hash(`${edge.id}:${index}:heading`) % 628) / 100,
        scale: 1.15 + (hash(`${edge.id}:${index}:scale`) % 25) / 100,
        cell: `${manifest.intersectionId}:core`,
      })
    })
  }
  return {
    facilities: {
      schemaVersion: 2,
      intersectionId: manifest.intersectionId,
      sourceGeneratedAt: new Date(0).toISOString(),
      lamps,
      cameras,
      signals: [],
      arrows: [],
      phaseTemplates: {},
    },
    vegetation: {
      schemaVersion: 1,
      source: { model: 'low-poly-plants.glb', license: 'unverified' },
      cellSizeMeters: 300,
      items: vegetation,
    },
  }
}

for (let index = 1; index <= 20; index += 1) {
  const id = `demo_${index}`
  const directory = new URL(`../public/intersections/v3/${id}/`, import.meta.url)
  const manifest = JSON.parse(await readFile(new URL('manifest.json', directory), 'utf8'))
  const generated = buildEnvironment(manifest)
  const environment = {
    schemaVersion: 1,
    intersectionId: id,
    facilitiesUrl: `/intersections/v3/${id}/facilities.json`,
    vegetation: {
      manifestUrl: `/intersections/v3/${id}/vegetation.json`,
      modelUrl: '/assets/plants/low-poly-plants.glb',
    },
    ...(id === 'demo_2' ? {
      geojson: {
        water: '/showcase-data/demo_2.water.geojson',
        green: '/showcase-data/demo_2.vegetation-ground.geojson',
        urban: '/showcase-data/demo_2.urban.geojson',
        buildings: '/showcase-data/demo_2.buildings.geojson',
      },
    } : {}),
  }
  await mkdir(directory, { recursive: true })
  await Promise.all([
    writeFile(new URL('environment.json', directory), `${JSON.stringify(environment, null, 2)}\n`),
    writeFile(new URL('facilities.json', directory), `${JSON.stringify(generated.facilities, null, 2)}\n`),
    writeFile(new URL('vegetation.json', directory), `${JSON.stringify(generated.vegetation, null, 2)}\n`),
  ])
}

console.log('Generated environment manifests for demo_1 through demo_20')
