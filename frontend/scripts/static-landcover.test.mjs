import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { checkStaticLandcover } from './check-static-landcover.mjs'

test('all 20 production landcover manifests resolve to valid static polygons', async () => {
  const report = await checkStaticLandcover(new URL('../public', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
  assert.equal(report.length, 20)
  assert.ok(report.some(row => row.green === 0)) // Empty surveyed source is valid.
  assert.ok(report.some(row => row.water > 0))
})

test('a missing static layer fails the deployment check', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'citypulse-landcover-'))
  try {
    const directory = path.join(root, 'intersections/v3/demo_1')
    await mkdir(directory, { recursive: true })
    await writeFile(path.join(directory, 'environment.json'), JSON.stringify({
      schemaVersion: 1, intersectionId: 'demo_1',
      geojson: { green: '/intersections/v3/demo_1/green.geojson', water: '/intersections/v3/demo_1/water.geojson' },
    }))
    await assert.rejects(checkStaticLandcover(root), /ENOENT/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
