import { readdir, readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

import { extractOsmLandcover } from './generate-showcase-landcover.mjs'

const PADDING_DEGREES = 0.02

function mergeBounds(left, right) {
  return {
    west: Math.min(left.west, right.west),
    south: Math.min(left.south, right.south),
    east: Math.max(left.east, right.east),
    north: Math.max(left.north, right.north),
  }
}

function boundsFromCollection(collection) {
  const raw = collection?.metadata?.bounds
  if (!Array.isArray(raw) || raw.length < 4 || !raw.every(Number.isFinite)) return null
  return {
    west: raw[0] - PADDING_DEGREES,
    south: raw[1] - PADDING_DEGREES,
    east: raw[2] + PADDING_DEGREES,
    north: raw[3] + PADDING_DEGREES,
  }
}

async function unionIntersectionBounds(directory) {
  let bounds = null
  for (const name of await readdir(directory)) {
    if (!name.startsWith('demo_')) continue
    for (const file of ['green.geojson', 'water.geojson']) {
      const next = boundsFromCollection(JSON.parse(
        await readFile(new URL(`${name}/${file}`, directory), 'utf8'),
      ))
      if (!next) continue
      bounds = bounds ? mergeBounds(bounds, next) : next
    }
  }
  if (!bounds) {
    throw new Error('Unable to derive full-network landcover bounds from intersection GeoJSON')
  }
  return bounds
}

async function main() {
  const intersections = new URL('../public/intersections/v3/', import.meta.url)
  const bounds = await unionIntersectionBounds(intersections)
  const osmXml = await readFile(new URL('../../data/maps/osm/TotalMap.osm', import.meta.url), 'utf8')
  const result = extractOsmLandcover(osmXml, bounds)
  const green = {
    ...result.green,
    metadata: {
      ...result.green.metadata,
      coverage: 'xiongan_20',
      fallback: 'Baidu vector base map',
    },
  }
  const water = {
    ...result.water,
    metadata: {
      ...result.water.metadata,
      coverage: 'xiongan_20',
      fallback: 'Baidu vector base map',
    },
  }
  await Promise.all([
    writeFile(new URL('full-landcover.green.geojson', intersections), `${JSON.stringify(green)}\n`),
    writeFile(new URL('full-landcover.water.geojson', intersections), `${JSON.stringify(water)}\n`),
  ])
  console.log(
    `Generated full landcover: ${green.features.length} green, ${water.features.length} water`,
    `bounds=${[bounds.west, bounds.south, bounds.east, bounds.north].map((value) => value.toFixed(5)).join(',')}`,
  )
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
