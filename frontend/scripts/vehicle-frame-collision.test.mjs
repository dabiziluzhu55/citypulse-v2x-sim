import assert from 'node:assert/strict'
import test from 'node:test'

import { auditVehicleFrameCollisions } from '../src/mapv/vehicleFrameCollisionAudit.ts'

const longitudeMeters = (meters) => meters / (111_320 * Math.cos(39 * Math.PI / 180))

function sample(id, visualMeters, sourceMeters) {
  return {
    id,
    point: [116 + longitudeMeters(visualMeters), 39, 0.3],
    dir: 0,
    time: 0,
    modelType: 3,
    scale: [1, 1, 1],
    color: '#fff',
    vehicleHeading: 0,
    modelForwardAxisAngle: 0,
    vehicleLengthMeters: 5,
    vehicleWidthMeters: 1.8,
    authoritativeSourceLongitude: 116 + longitudeMeters(sourceMeters),
    authoritativeSourceLatitude: 39,
  }
}

test('rejects only a visual mapping intersection that SUMO source space did not contain', () => {
  const result = auditVehicleFrameCollisions([
    sample('vehicle-a', 0, 0),
    sample('vehicle-b', 2, 10),
  ])
  assert.equal(result.sourceIntersectionCount, 0)
  assert.equal(result.visualAddedIntersectionCount, 1)
  assert.deepEqual(result.rejectedVehicleIds, ['vehicle-b'])
  assert.deepEqual(result.acceptedSamples.map((item) => item.id), ['vehicle-a'])
})

test('records a SUMO source intersection without moving or hiding either vehicle', () => {
  const result = auditVehicleFrameCollisions([
    sample('vehicle-a', 0, 0),
    sample('vehicle-b', 2, 2),
  ])
  assert.equal(result.sourceIntersectionCount, 1)
  assert.equal(result.visualAddedIntersectionCount, 0)
  assert.equal(result.acceptedSamples.length, 2)
})
