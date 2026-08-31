import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const networkPath = new URL('../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml', import.meta.url)
const assetPath = new URL('../public/intersections/v3/full-road-network.geojson', import.meta.url)

test('full 2D road network matches the current non-internal SUMO network', async () => {
  const [source, assetSource] = await Promise.all([
    readFile(networkPath),
    readFile(assetPath, 'utf8'),
  ])
  const asset = JSON.parse(assetSource)
  assert.equal(asset.type, 'FeatureCollection')
  assert.equal(asset.metadata.sourceSha256, createHash('sha256').update(source).digest('hex'))
  assert.equal(asset.metadata.edgeCount, 12_477)
  assert.equal(asset.features.length, asset.metadata.edgeCount)
  assert.deepEqual(asset.metadata.bounds, {
    west: 115.75662679238573,
    south: 38.93723746758801,
    east: 116.2349976369637,
    north: 39.158068485701115,
  })
  assert.ok(asset.features.every((feature) => (
    !String(feature.properties.edge_id).startsWith(':')
    && feature.geometry.type === 'LineString'
    && feature.geometry.coordinates.length >= 2
    && feature.geometry.coordinates.every(([longitude, latitude]) => (
      Number.isFinite(longitude)
      && Number.isFinite(latitude)
      && longitude >= -180 && longitude <= 180
      && latitude >= -90 && latitude <= 90
    ))
  )))
})
