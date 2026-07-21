import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { Cartesian3, Cartographic, Ellipsoid } from 'cesium'

import {
  gcj02ToBd09,
  projectBd09ToWebMercator,
  wgs84ToBd09,
} from '../src/mapv/sceneCoordinates.ts'

const GLB_JSON_CHUNK = 0x4e4f534a
const GLB_BIN_CHUNK = 0x004e4942
const FLOAT_COMPONENT = 5126

function sourceCoordinateToBd09(longitude, latitude, sourceCrs) {
  if (sourceCrs === 'wgs84') return wgs84ToBd09(longitude, latitude)
  if (sourceCrs === 'gcj02') return gcj02ToBd09(longitude, latitude)
  if (sourceCrs === 'bd09') return [longitude, latitude]
  throw new Error(`Unsupported source CRS: ${sourceCrs}`)
}

function applyMatrix4(matrix, [x, y, z]) {
  return [
    matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
    matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
    matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
  ]
}

export function createBuildingProjector(rootTransform, sourceCrs = 'wgs84') {
  if (!Array.isArray(rootTransform) || rootTransform.length !== 16) {
    throw new Error('Tileset root transform must contain 16 numbers')
  }
  const ellipsoid = Ellipsoid.WGS84
  const cartesian = new Cartesian3()
  const cartographic = new Cartographic()
  const originEcef = applyMatrix4(rootTransform, [0, 0, 0])
  Cartesian3.fromArray(originEcef, 0, cartesian)
  ellipsoid.cartesianToCartographic(cartesian, cartographic)
  const originHeight = cartographic.height
  const originDegrees = [
    cartographic.longitude * 180 / Math.PI,
    cartographic.latitude * 180 / Math.PI,
  ]
  const originBd09 = sourceCoordinateToBd09(...originDegrees, sourceCrs)
  const originPlane = projectBd09ToWebMercator(originBd09)

  const project = (point) => {
    Cartesian3.fromArray(applyMatrix4(rootTransform, point), 0, cartesian)
    ellipsoid.cartesianToCartographic(cartesian, cartographic)
    const longitude = cartographic.longitude * 180 / Math.PI
    const latitude = cartographic.latitude * 180 / Math.PI
    const plane = projectBd09ToWebMercator(sourceCoordinateToBd09(
      longitude,
      latitude,
      sourceCrs,
    ))
    return [
      plane[0] - originPlane[0],
      plane[1] - originPlane[1],
      cartographic.height - originHeight,
    ]
  }
  project.originBd09 = originBd09
  project.originPlane = originPlane
  project.originHeight = originHeight
  project.originLatitude = originDegrees[1]
  return project
}

export function parseGlb(input) {
  const glb = Buffer.from(input)
  if (glb.subarray(0, 4).toString('utf8') !== 'glTF') throw new Error('Invalid GLB magic')
  if (glb.readUInt32LE(4) !== 2) throw new Error('Only GLB 2.0 is supported')
  if (glb.readUInt32LE(8) !== glb.length) throw new Error('Invalid GLB byte length')

  let jsonChunk = null
  let binaryChunk = null
  for (let offset = 12; offset < glb.length;) {
    const length = glb.readUInt32LE(offset)
    const type = glb.readUInt32LE(offset + 4)
    const value = glb.subarray(offset + 8, offset + 8 + length)
    if (type === GLB_JSON_CHUNK) jsonChunk = value
    if (type === GLB_BIN_CHUNK) binaryChunk = value
    offset += 8 + length
  }
  if (!jsonChunk || !binaryChunk) throw new Error('GLB must contain JSON and BIN chunks')
  return {
    json: JSON.parse(jsonChunk.toString('utf8').trimEnd()),
    binary: Buffer.from(binaryChunk),
  }
}

function encodeGlb(json, binary) {
  const jsonData = Buffer.from(JSON.stringify(json))
  const jsonPadding = (4 - jsonData.length % 4) % 4
  const paddedJson = Buffer.concat([jsonData, Buffer.alloc(jsonPadding, 0x20)])
  const binaryPadding = (4 - binary.length % 4) % 4
  const paddedBinary = Buffer.concat([binary, Buffer.alloc(binaryPadding)])
  const output = Buffer.alloc(12 + 8 + paddedJson.length + 8 + paddedBinary.length)
  output.write('glTF', 0)
  output.writeUInt32LE(2, 4)
  output.writeUInt32LE(output.length, 8)
  output.writeUInt32LE(paddedJson.length, 12)
  output.writeUInt32LE(GLB_JSON_CHUNK, 16)
  paddedJson.copy(output, 20)
  const binaryHeader = 20 + paddedJson.length
  output.writeUInt32LE(paddedBinary.length, binaryHeader)
  output.writeUInt32LE(GLB_BIN_CHUNK, binaryHeader + 4)
  paddedBinary.copy(output, binaryHeader + 8)
  return output
}

function includePoint(bounds, point) {
  for (let index = 0; index < 3; index += 1) {
    bounds.min[index] = Math.min(bounds.min[index], point[index])
    bounds.max[index] = Math.max(bounds.max[index], point[index])
  }
}

export function reprojectGlb(input, project) {
  const { json, binary } = parseGlb(input)
  const positionAccessors = new Set()
  for (const mesh of json.meshes ?? []) {
    for (const primitive of mesh.primitives ?? []) {
      if (primitive.attributes?.POSITION !== undefined) {
        positionAccessors.add(primitive.attributes.POSITION)
      }
    }
  }
  if (positionAccessors.size === 0) throw new Error('GLB contains no POSITION accessor')
  const bounds = {
    min: [Infinity, Infinity, Infinity],
    max: [-Infinity, -Infinity, -Infinity],
  }
  let vertexCount = 0

  for (const accessorIndex of positionAccessors) {
    const accessor = json.accessors?.[accessorIndex]
    const view = json.bufferViews?.[accessor?.bufferView]
    if (!accessor || !view) throw new Error(`Missing POSITION accessor ${accessorIndex}`)
    if (accessor.componentType !== FLOAT_COMPONENT || accessor.type !== 'VEC3' || accessor.sparse) {
      throw new Error(`POSITION accessor ${accessorIndex} must be a non-sparse FLOAT VEC3`)
    }
    if ((view.buffer ?? 0) !== 0) throw new Error('Only the primary GLB buffer is supported')
    const stride = view.byteStride ?? 12
    if (stride < 12) throw new Error(`Invalid POSITION byte stride: ${stride}`)
    const start = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0)
    const data = new DataView(binary.buffer, binary.byteOffset, binary.byteLength)
    const accessorBounds = {
      min: [Infinity, Infinity, Infinity],
      max: [-Infinity, -Infinity, -Infinity],
    }
    for (let index = 0; index < accessor.count; index += 1) {
      const offset = start + index * stride
      const point = project([
        data.getFloat32(offset, true),
        data.getFloat32(offset + 4, true),
        data.getFloat32(offset + 8, true),
      ]).map(Math.fround)
      if (!point.every(Number.isFinite)) throw new Error('Projection produced a non-finite vertex')
      data.setFloat32(offset, point[0], true)
      data.setFloat32(offset + 4, point[1], true)
      data.setFloat32(offset + 8, point[2], true)
      includePoint(accessorBounds, point)
      includePoint(bounds, point)
    }
    accessor.min = accessorBounds.min
    accessor.max = accessorBounds.max
    vertexCount += accessor.count
  }
  return { glb: encodeGlb(json, binary), bounds, vertexCount }
}

function boxFromBounds({ min, max }) {
  const center = min.map((value, index) => (value + max[index]) / 2)
  const half = min.map((value, index) => (max[index] - value) / 2)
  return [
    center[0], center[1], center[2],
    half[0], 0, 0,
    0, half[1], 0,
    0, 0, Math.max(half[2], 0.5),
  ]
}

function webMercatorTransform([x, y]) {
  return [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    x, y, 0, 1,
  ]
}

function assertSimpleGlb(json, uri) {
  for (const node of json.nodes ?? []) {
    if (node.matrix || node.translation || node.rotation || node.scale) {
      throw new Error(`${uri} contains a node transform that requires baking`)
    }
  }
  if (json.extensionsRequired?.length) {
    throw new Error(`${uri} requires unsupported GLB extensions`)
  }
}

export async function reprojectTileset({
  sourceDirectory,
  outputDirectory,
  sourceCrs = 'wgs84',
  limit = Infinity,
}) {
  const sourceTileset = JSON.parse(await readFile(path.join(sourceDirectory, 'tileset.json'), 'utf8'))
  const project = createBuildingProjector(sourceTileset.root.transform, sourceCrs)
  const children = sourceTileset.root.children.slice(0, limit)
  const outputChildren = []
  const rootBounds = {
    min: [Infinity, Infinity, Infinity],
    max: [-Infinity, -Infinity, -Infinity],
  }
  let vertexCount = 0

  for (const child of children) {
    const uri = child.content?.uri
    if (!uri) throw new Error(`Tile ${child.id ?? '<unknown>'} has no content URI`)
    const sourceGlb = await readFile(path.join(sourceDirectory, uri))
    assertSimpleGlb(parseGlb(sourceGlb).json, uri)
    const result = reprojectGlb(sourceGlb, project)
    const target = path.join(outputDirectory, uri)
    await mkdir(path.dirname(target), { recursive: true })
    await writeFile(target, result.glb)
    result.bounds.min.forEach((value, index) => {
      rootBounds.min[index] = Math.min(rootBounds.min[index], value)
      rootBounds.max[index] = Math.max(rootBounds.max[index], result.bounds.max[index])
    })
    outputChildren.push({
      ...child,
      boundingVolume: { box: boxFromBounds(result.bounds) },
    })
    vertexCount += result.vertexCount
  }

  const scale = 1 / Math.cos(project.originLatitude * Math.PI / 180)
  const outputTileset = {
    ...sourceTileset,
    geometricError: sourceTileset.geometricError * scale,
    root: {
      ...sourceTileset.root,
      transform: webMercatorTransform(project.originPlane),
      boundingVolume: { box: boxFromBounds(rootBounds) },
      geometricError: sourceTileset.root.geometricError * scale,
      children: outputChildren,
    },
  }
  const manifest = {
    source_crs: sourceCrs,
    coordinate_system: 'LOCAL_BD09_WEB_MERCATOR_METERS',
    origin_bd09: project.originBd09,
    origin_web_mercator: project.originPlane,
    source_tile_count: sourceTileset.root.children.length,
    output_tile_count: outputChildren.length,
    vertex_count: vertexCount,
  }
  await mkdir(outputDirectory, { recursive: true })
  await Promise.all([
    writeFile(path.join(outputDirectory, 'tileset.json'), `${JSON.stringify(outputTileset, null, 2)}\n`),
    writeFile(path.join(outputDirectory, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`),
  ])
  return { tileset: outputTileset, manifest }
}

async function main() {
  const sourceDirectory = path.resolve(process.argv[2] ?? 'public/3dtiles/xiongan')
  const outputDirectory = path.resolve(process.argv[3] ?? 'public/3dtiles/xiongan-webmercator')
  const sourceCrs = process.argv[4] ?? 'wgs84'
  const limit = process.argv[5] ? Number(process.argv[5]) : Infinity
  const result = await reprojectTileset({ sourceDirectory, outputDirectory, sourceCrs, limit })
  console.log(`Generated ${result.manifest.output_tile_count} tiles / ${result.manifest.vertex_count} vertices in ${path.relative(process.cwd(), outputDirectory)}`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main()
}
