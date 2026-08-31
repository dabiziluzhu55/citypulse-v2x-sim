import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const expectedSourceHashes = {
  red: 'dc68d74313ace5761e19c695ca0ff3e51f65fed07a567df6fa826f129bfe6be5',
  yellow: 'bb5a61bcee3780801048a2dc3f453d8ea6a9f4432ade92b75d403011b7ec4257',
}

function jsonChunk(buffer) {
  assert.equal(buffer.readUInt32LE(0), 0x46546c67)
  assert.equal(buffer.readUInt32LE(4), 2)
  const length = buffer.readUInt32LE(12)
  assert.equal(buffer.readUInt32LE(16), 0x4e4f534a)
  return JSON.parse(buffer.subarray(20, 20 + length).toString('utf8').trim())
}

test('optimized event marker assets meet the runtime budget and compression contract', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../public/models/events/manifest.json', import.meta.url),
    'utf8',
  ))
  assert.equal(manifest.schemaVersion, 1)
  assert.deepEqual(manifest.models.map((model) => model.kind).sort(), ['red', 'yellow'])
  for (const model of manifest.models) {
    const assetName = `event-marker-${model.kind}.glb`
    const buffer = await readFile(new URL(`../public/models/events/${assetName}`, import.meta.url))
    const gltf = jsonChunk(buffer)
    assert.equal(model.sourceSha256, expectedSourceHashes[model.kind])
    assert.equal(createHash('sha256').update(buffer).digest('hex'), model.outputSha256)
    assert.ok(buffer.length <= 3 * 1024 * 1024, `${assetName} exceeds 3 MiB`)
    assert.ok(model.triangles <= 60_000, `${assetName} exceeds 60k triangles`)
    assert.ok(model.textures.every((texture) => (
      texture.width <= 1024 && texture.height <= 1024 && texture.mimeType === 'image/webp'
    )))
    assert.ok(gltf.extensionsRequired.includes('EXT_meshopt_compression'))
    assert.ok(gltf.extensionsRequired.includes('EXT_texture_webp'))
  }
})
