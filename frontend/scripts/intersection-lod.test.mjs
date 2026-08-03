import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  resolveIntersectionRoadLod,
} from '../src/mapv/realistic/intersectionLod.ts'

test('keeps the selected intersection fully detailed at every range', () => {
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 48_000,
    distanceMeters: 30_000,
    active: true,
  }), 'full')
})

test('promotes any nearby unselected intersection as the camera approaches', () => {
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 12_000,
    distanceMeters: 500,
    active: false,
  }), 'overview')
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 4_000,
    distanceMeters: 1_000,
    active: false,
  }), 'medium')
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 1_200,
    distanceMeters: 1_000,
    active: false,
  }), 'full')
})

test('uses fifteen percent hysteresis around road detail boundaries', () => {
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 2_250,
    distanceMeters: 2_750,
    active: false,
    previous: 'full',
  }), 'full')
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 2_350,
    distanceMeters: 2_900,
    active: false,
    previous: 'full',
  }), 'medium')
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 8_900,
    distanceMeters: 1_000,
    active: false,
    previous: 'medium',
  }), 'medium')
  assert.equal(resolveIntersectionRoadLod({
    cameraRangeMeters: 9_300,
    distanceMeters: 1_000,
    active: false,
    previous: 'medium',
  }), 'overview')
})

test('protects a road detail object until its intersection activation finishes', async () => {
  const source = await readFile(
    new URL('../src/mapv/realistic/MapvRealisticIntersectionLayer.ts', import.meta.url),
    'utf8',
  )
  assert.match(source, /private readonly pendingActivations = new Set<string>\(\)/)
  assert.match(source, /this\.pendingActivations\.add\(intersectionId\)/)
  assert.match(source, /!this\.pendingActivations\.has\(id\)/)
  assert.match(source, /!this\.detailRequests\.has\(id\)/)
})
