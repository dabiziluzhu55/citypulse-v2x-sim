import { mkdir, readFile, writeFile } from 'node:fs/promises'

import { buildSceneFacilityManifest } from '../src/mapv/showcaseLayers/sceneFacilities.ts'

const roadsUrl = new URL('../public/showcase-data/demo_2.roads.wgs84.geojson', import.meta.url)
const tlsUrl = new URL('../../data/maps/sumo/generated/manifests/tls_manifest.json', import.meta.url)
const outputUrl = new URL('../public/showcase-data/demo_2.facilities.json', import.meta.url)

const [roads, tls] = await Promise.all([
  readFile(roadsUrl, 'utf8').then(JSON.parse),
  readFile(tlsUrl, 'utf8').then(JSON.parse),
])
const manifest = buildSceneFacilityManifest(roads, tls, 'demo_2')

await mkdir(new URL('.', outputUrl), { recursive: true })
await writeFile(outputUrl, `${JSON.stringify(manifest, null, 2)}\n`)
console.log(`Generated ${outputUrl.pathname}: ${manifest.lamps.length} lamps, ${manifest.signals.length} signals, ${manifest.arrows.length} arrows`)
