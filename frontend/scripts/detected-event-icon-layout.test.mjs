// 图标网格错位：按 event_id 稳定、相近可分开

import assert from 'node:assert/strict'
import test from 'node:test'

const DEFAULT_CELL_SIZE_PX = 36
const DEFAULT_RING_RADIUS_PX = 18

function ringOffset(index, count, radius) {
  if (count <= 1) return { offsetX: 0, offsetY: 0 }
  const angle = (-Math.PI / 2) + (index * 2 * Math.PI) / count
  return {
    offsetX: Math.cos(angle) * radius,
    offsetY: Math.sin(angle) * radius,
  }
}

function layoutDetectedEventIcons(markers, options = {}) {
  if (markers.length === 0) return []
  const cellSize = options.cellSizePx ?? DEFAULT_CELL_SIZE_PX
  const ringRadius = options.ringRadiusPx ?? DEFAULT_RING_RADIUS_PX
  const groups = new Map()
  for (const marker of markers) {
    const cellX = Math.floor(marker.x / cellSize)
    const cellY = Math.floor(marker.y / cellSize)
    const key = `${cellX}:${cellY}`
    const bucket = groups.get(key)
    if (bucket) bucket.push(marker)
    else groups.set(key, [marker])
  }
  const laidOut = []
  for (const bucket of groups.values()) {
    bucket.sort((left, right) => left.eventId.localeCompare(right.eventId))
    bucket.forEach((marker, index) => {
      const offset = ringOffset(index, bucket.length, ringRadius)
      laidOut.push({
        eventId: marker.eventId,
        x: marker.x + offset.offsetX,
        y: marker.y + offset.offsetY,
        offsetX: offset.offsetX,
        offsetY: offset.offsetY,
      })
    })
  }
  return laidOut.sort((left, right) => left.eventId.localeCompare(right.eventId))
}

function detectedEventLayoutKey(markers, viewToken = '', cellSizePx = DEFAULT_CELL_SIZE_PX) {
  const parts = markers
    .map((marker) => {
      const cellX = Math.floor(marker.x / cellSizePx)
      const cellY = Math.floor(marker.y / cellSizePx)
      return `${marker.eventId}@${cellX},${cellY}`
    })
    .sort()
  return `${viewToken}|${parts.join(';')}`
}

test('nearby icons get deterministic offsets sorted by event_id', () => {
  const laid = layoutDetectedEventIcons([
    { eventId: 'b', x: 100, y: 100 },
    { eventId: 'a', x: 102, y: 101 },
  ])
  assert.equal(laid.length, 2)
  assert.equal(laid[0].eventId, 'a')
  assert.equal(laid[1].eventId, 'b')
  assert.notEqual(laid[0].x, laid[1].x)
  const again = layoutDetectedEventIcons([
    { eventId: 'a', x: 102, y: 101 },
    { eventId: 'b', x: 100, y: 100 },
  ])
  assert.deepEqual(
    again.map((item) => [item.eventId, item.offsetX, item.offsetY]),
    laid.map((item) => [item.eventId, item.offsetX, item.offsetY]),
  )
})

test('layout key stable for same cells and events', () => {
  const markers = [
    { eventId: 'a', x: 10, y: 10 },
    { eventId: 'b', x: 200, y: 200 },
  ]
  assert.equal(detectedEventLayoutKey(markers, 'v1'), detectedEventLayoutKey([...markers].reverse(), 'v1'))
})
