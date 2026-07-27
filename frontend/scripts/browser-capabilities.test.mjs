import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveMap3dCapability } from '../src/mapv/map3dCapabilities.ts'

test('falls back when WebGL is unavailable', () => {
  const capability = resolveMap3dCapability({ webgl: false, webgl2: false, hardwareConcurrency: 8 })
  assert.equal(capability.supported, false)
  assert.equal(capability.quality, 'unsupported')
})

test('selects reduced quality for constrained WebGL devices', () => {
  const capability = resolveMap3dCapability({
    webgl: true,
    webgl2: false,
    hardwareConcurrency: 2,
    deviceMemory: 2,
  })
  assert.equal(capability.supported, true)
  assert.equal(capability.quality, 'reduced')
})

test('enables the full renderer on capable devices', () => {
  const capability = resolveMap3dCapability({
    webgl: true,
    webgl2: true,
    hardwareConcurrency: 8,
    deviceMemory: 8,
  })
  assert.equal(capability.quality, 'full')
})
