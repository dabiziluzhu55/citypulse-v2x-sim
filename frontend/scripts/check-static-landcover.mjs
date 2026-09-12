import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

// Also accepts an HTTP origin to verify the actual Nginx delivery chain.
export async function checkStaticLandcover(root = 'public') {
  const remote = /^https?:\/\//.test(root)
  async function read(url) {
    if (!url.startsWith('/intersections/v3/') || url.includes('..') || url.includes('\\')) {
      throw new Error(`Invalid static landcover URL: ${url}`)
    }
    if (!remote) return JSON.parse(await readFile(path.join(root, url.slice(1)), 'utf8'))
    const response = await fetch(new URL(url, root))
    if (!response.ok || !response.headers.get('content-type')?.includes('json')) {
      throw new Error(`Static resource failed: ${url} HTTP ${response.status}`)
    }
    return response.json()
  }
  const report = []
  for (let n = 1; n <= 20; n++) {
    const id = `demo_${n}`
    const manifest = await read(`/intersections/v3/${id}/environment.json`)
    if (manifest.schemaVersion !== 1 || manifest.intersectionId !== id) throw new Error(`Invalid manifest: ${id}`)
    const counts = {}
    for (const kind of ['green', 'water']) {
      const expected = `/intersections/v3/${id}/${kind}.geojson`
      if (manifest.geojson?.[kind] !== expected) throw new Error(`Missing ${kind} mapping: ${id}`)
      const data = await read(expected)
      if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) throw new Error(`Invalid GeoJSON: ${expected}`)
      for (const feature of data.features) {
        if (feature.type !== 'Feature' || !['Polygon', 'MultiPolygon'].includes(feature.geometry?.type)) {
          throw new Error(`Invalid landcover geometry: ${expected}`)
        }
        const polygons = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates
        for (const polygon of polygons) for (const ring of polygon) {
          if (ring.length < 4 || JSON.stringify(ring[0]) !== JSON.stringify(ring.at(-1))) throw new Error(`Unclosed ring: ${expected}`)
          for (const [lon, lat] of ring) {
            if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) throw new Error(`Invalid coordinate: ${expected}`)
          }
        }
      }
      counts[kind] = data.features.length
    }
    report.push({ id, ...counts })
  }
  return report
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const report = await checkStaticLandcover(process.argv[2] || 'public')
  console.log(JSON.stringify({ checked: report.length, emptySources: report.filter(row => !row.green || !row.water), report }, null, 2))
}
