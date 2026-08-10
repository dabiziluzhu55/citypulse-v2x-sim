import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const GREEN_TAGS = {
  landuse: new Set([
    'allotments',
    'cemetery',
    'farmland',
    'forest',
    'grass',
    'meadow',
    'orchard',
    'plant_nursery',
    'recreation_ground',
    'village_green',
  ]),
  leisure: new Set(['garden', 'golf_course', 'park', 'pitch']),
  natural: new Set(['grassland', 'heath', 'scrub', 'wetland', 'wood']),
}
const URBAN_LANDUSE = new Set(['residential', 'construction', 'commercial', 'industrial', 'retail'])

function attributes(source) {
  return Object.fromEntries(
    [...source.matchAll(/([\w:-]+)="([^"]*)"/g)].map((match) => [match[1], match[2]]),
  )
}

function collection(features) {
  return { type: 'FeatureCollection', features }
}

function polygonIntersectsBounds(coordinates, bounds) {
  const longitudes = coordinates.map(([longitude]) => longitude)
  const latitudes = coordinates.map(([, latitude]) => latitude)
  return Math.min(...longitudes) <= bounds.east
    && Math.max(...longitudes) >= bounds.west
    && Math.min(...latitudes) <= bounds.north
    && Math.max(...latitudes) >= bounds.south
}

function landcoverKind(tags) {
  if (
    tags.natural === 'water'
    || tags.water
    || tags.waterway === 'riverbank'
    || tags.landuse === 'basin'
    || tags.landuse === 'reservoir'
  ) return 'water'
  if (Object.entries(GREEN_TAGS).some(([key, values]) => values.has(tags[key]))) return 'green'
  return URBAN_LANDUSE.has(tags.landuse) ? 'urban' : null
}

function buildingHeight(tags) {
  const explicit = Number.parseFloat(String(tags.height ?? '').replace(',', '.'))
  if (Number.isFinite(explicit) && explicit > 0) return Math.min(120, Math.max(4, explicit))
  const levels = Number.parseFloat(String(tags['building:levels'] ?? '').replace(',', '.'))
  if (Number.isFinite(levels) && levels > 0) return Math.min(120, Math.max(4, levels * 3))
  return 15
}

export function extractOsmLandcover(osmXml, bounds) {
  const nodes = new Map()
  for (const match of osmXml.matchAll(/<node\b([^>]*)\/?\s*>/g)) {
    const value = attributes(match[1])
    const longitude = Number(value.lon)
    const latitude = Number(value.lat)
    if (value.id && Number.isFinite(longitude) && Number.isFinite(latitude)) {
      nodes.set(value.id, [longitude, latitude])
    }
  }

  const features = { green: [], water: [], urban: [], buildings: [] }
  // ponytail: the checked-in OSM uses ordinary closed ways here; add relation support only if needed.
  for (const match of osmXml.matchAll(/<way\b([^>]*)>([\s\S]*?)<\/way>/g)) {
    const way = attributes(match[1])
    const body = match[2]
    const refs = [...body.matchAll(/<nd\b([^>]*)\/?\s*>/g)]
      .map((candidate) => attributes(candidate[1]).ref)
      .filter(Boolean)
    if (!way.id || refs.length < 4 || refs[0] !== refs.at(-1)) continue
    const tags = Object.fromEntries(
      [...body.matchAll(/<tag\b([^>]*)\/?\s*>/g)].map((candidate) => {
        const tag = attributes(candidate[1])
        return [tag.k, tag.v]
      }).filter(([key]) => key),
    )
    const kind = tags.building && tags.building !== 'no' ? 'buildings' : landcoverKind(tags)
    if (!kind) continue
    const coordinates = refs.map((ref) => nodes.get(ref)).filter(Boolean)
    if (coordinates.length !== refs.length || !polygonIntersectsBounds(coordinates, bounds)) continue
    features[kind].push({
      type: 'Feature',
      id: `way/${way.id}`,
      properties: {
        osm_id: way.id,
        class: kind === 'buildings' ? 'building' : kind,
        ...(kind === 'buildings' ? { height: buildingHeight(tags) } : {}),
      },
      geometry: { type: 'Polygon', coordinates: [coordinates] },
    })
  }

  for (const kind of ['green', 'water', 'urban', 'buildings']) {
    features[kind].sort((a, b) => a.properties.osm_id.localeCompare(
      b.properties.osm_id,
      undefined,
      { numeric: true },
    ))
  }
  const metadata = {
    source: 'OpenStreetMap closed ways',
    bounds: [bounds.west, bounds.south, bounds.east, bounds.north],
  }
  return Object.fromEntries(
    Object.entries(features).map(([kind, items]) => [kind, {
      ...collection(items),
      metadata: { ...metadata, kind, featureCount: items.length },
    }]),
  )
}

async function main() {
  const osmUrl = new URL('../../data/maps/osm/TotalMap.osm', import.meta.url)
  const roadsUrl = new URL('../public/showcase-data/demo_2.roads.wgs84.geojson', import.meta.url)
  const roads = JSON.parse(await readFile(roadsUrl, 'utf8'))
  const center = roads.metadata.center
  const radius = Math.max(Number(roads.metadata.radius_m ?? 600) + 100, 1800)
  const latitudeDelta = radius / 110_900
  const longitudeDelta = radius / (Math.cos(center.latitude * Math.PI / 180) * 110_900)
  const result = extractOsmLandcover(await readFile(osmUrl, 'utf8'), {
    west: center.longitude - longitudeDelta,
    south: center.latitude - latitudeDelta,
    east: center.longitude + longitudeDelta,
    north: center.latitude + latitudeDelta,
  })
  const output = new URL('../public/showcase-data/', import.meta.url)
  await Promise.all([
    writeFile(new URL('demo_2.green.geojson', output), `${JSON.stringify(result.green, null, 2)}\n`),
    writeFile(new URL('demo_2.water.geojson', output), `${JSON.stringify(result.water, null, 2)}\n`),
    writeFile(new URL('demo_2.urban.geojson', output), `${JSON.stringify(result.urban, null, 2)}\n`),
    writeFile(new URL('demo_2.buildings.geojson', output), `${JSON.stringify(result.buildings, null, 2)}\n`),
  ])
  console.log(`Generated landcover: ${result.green.features.length} green, ${result.water.features.length} water, ${result.urban.features.length} urban, ${result.buildings.features.length} buildings`)
}

if (
  process.argv[1]
  && process.argv[1] !== '-'
  && fileURLToPath(import.meta.url) === fileURLToPath(new URL(`file:///${process.argv[1].replace(/\\/g, '/')}`))
) {
  await main()
}
