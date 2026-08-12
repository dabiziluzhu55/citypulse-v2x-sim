import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveMap3dCapability } from '../src/mapv/map3dCapabilities.ts'
import {
  classifyMap3dFailure,
  shouldAutomaticallyRecoverWebgl,
} from '../src/mapv/map3dLoadRecovery.ts'

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

test('identifies a browser disk cache read failure', () => {
  const failure = classifyMap3dFailure(new Error('net::ERR_CACHE_READ_FAILURE'))

  assert.equal(failure.code, 'module-cache')
  assert.match(failure.message, /浏览器/)
})

test('identifies a failed dynamic module import', () => {
  const failure = classifyMap3dFailure(
    new TypeError('Failed to fetch dynamically imported module: /src/BaiduThreeMap.vue'),
  )

  assert.equal(failure.code, 'module-load')
  assert.match(failure.message, /3D模块/)
})

test('keeps WebGL and scene asset failures distinguishable', () => {
  assert.equal(classifyMap3dFailure(new Error('WebGL context lost')).code, 'webgl')
  assert.equal(classifyMap3dFailure(new Error('3D Tiles manifest 加载失败')).code, 'scene-assets')
})

test('automatically rebuilds WebGL once per 60 second recovery window', () => {
  assert.equal(shouldAutomaticallyRecoverWebgl(null, 1_000), true)
  assert.equal(shouldAutomaticallyRecoverWebgl(1_000, 60_999), false)
  assert.equal(shouldAutomaticallyRecoverWebgl(1_000, 61_001), true)
})
