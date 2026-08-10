import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { buildFocusedTileset } from './focus-building-tileset.mjs'

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendDirectory = path.resolve(scriptsDirectory, '..')
const sourceDirectory = path.resolve(frontendDirectory, 'public/3dtiles/xiongan-webmercator')
const outputRoot = path.resolve(frontendDirectory, 'public/3dtiles/intersections')
const mapping = JSON.parse(await readFile(
  path.resolve(frontendDirectory, '../data/maps/sumo/TotalMap_20.intersections.json'),
  'utf8',
))
const radiusMeters = Number(process.argv[2] ?? 650)

for (let index = 1; index <= 20; index += 1) {
  const intersectionId = `demo_${index}`
  const location = mapping[intersectionId]
  if (!location) throw new Error(`Missing mapping for ${intersectionId}`)
  const result = await buildFocusedTileset({
    sourceDirectory,
    outputDirectory: path.resolve(outputRoot, intersectionId),
    centerWgs84: [Number(location.junction_lon), Number(location.junction_lat)],
    radiusMeters,
  })
  console.log(`${intersectionId}: ${result.manifest.output_tile_count} tiles, ${result.manifest.triangle_count} triangles`)
}
