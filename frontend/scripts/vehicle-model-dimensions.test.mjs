import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { Box3, Vector3 } from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

import {
  BUS_MODEL_PROFILE,
  CAR_MODEL_PROFILE,
  ELECTRIC_BICYCLE_MODEL_PROFILE,
  TRUCK_MODEL_PROFILE,
} from '../src/mapv/vehicleModelProfiles.ts'

globalThis.self = globalThis
globalThis.ProgressEvent ??= class ProgressEvent {}
globalThis.createImageBitmap ??= async () => ({ width: 1, height: 1, close() {} })

const assets = [
  ['CAR', CAR_MODEL_PROFILE],
  ['BUS', BUS_MODEL_PROFILE],
  ['TRUCK', TRUCK_MODEL_PROFILE],
  ['ELECTRICBICYCLE', ELECTRIC_BICYCLE_MODEL_PROFILE],
]

test('calibrates every MapV vehicle GLB to its SUMO physical length and width', async () => {
  const loader = new GLTFLoader()
  for (const [name, profile] of assets) {
    const source = await readFile(new URL(
      `../node_modules/@baidumap/mapv-three/dist/assets/models/twin/REALISTIC/${name}.glb`,
      import.meta.url,
    ))
    const buffer = source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength)
    const gltf = await loader.parseAsync(buffer, '')
    gltf.scene.updateMatrixWorld(true)
    const size = new Box3().setFromObject(gltf.scene).getSize(new Vector3())

    assert.ok(Math.abs(size.x - profile.sourceLengthMeters) <= 0.005, `${name} source length`)
    assert.ok(Math.abs(size.z - profile.sourceWidthMeters) <= 0.005, `${name} source width`)
    assert.ok(Math.abs(size.y - profile.sourceHeightMeters) <= 0.005, `${name} source height`)
    assert.ok(
      Math.abs(size.x * profile.scale[0] - profile.targetLengthMeters) <= 0.005,
      `${name} SUMO length`,
    )
    assert.ok(
      Math.abs(size.z * profile.scale[2] - profile.targetWidthMeters) <= 0.005,
      `${name} SUMO width`,
    )
  }
})
