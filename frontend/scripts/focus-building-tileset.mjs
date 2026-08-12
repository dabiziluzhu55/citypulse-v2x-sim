import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { encodeGlb, parseGlb } from './reproject-building-tileset.mjs'
import { projectBd09ToWebMercator, wgs84ToBd09 } from '../src/mapv/sceneCoordinates.ts'

const UNSIGNED_SHORT = 5123
const UNSIGNED_INT = 5125
const FLOAT = 5126

function align4(value) {
  return (value + 3) & ~3
}

function accessorView(json, binary, index, components) {
  const accessor = json.accessors[index]
  const view = json.bufferViews[accessor.bufferView]
  const offset = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0)
  const bytesPerComponent = accessor.componentType === UNSIGNED_SHORT ? 2 : 4
  const stride = view.byteStride ?? bytesPerComponent * components
  const data = new DataView(binary.buffer, binary.byteOffset, binary.byteLength)
  const read = accessor.componentType === FLOAT
    ? (byteOffset) => data.getFloat32(byteOffset, true)
    : accessor.componentType === UNSIGNED_INT
      ? (byteOffset) => data.getUint32(byteOffset, true)
      : accessor.componentType === UNSIGNED_SHORT
        ? (byteOffset) => data.getUint16(byteOffset, true)
        : null
  if (!read) throw new Error(`Unsupported accessor component type ${accessor.componentType}`)
  return {
    accessor,
    get(item, component = 0) {
      return read(offset + item * stride + component * bytesPerComponent)
    },
  }
}

export function extractGlbTriangles(input) {
  const { json, binary } = parseGlb(input)
  const triangles = []
  for (const mesh of json.meshes ?? []) {
    for (const primitive of mesh.primitives ?? []) {
      if ((primitive.mode ?? 4) !== 4 || primitive.attributes?.POSITION === undefined) continue
      const position = accessorView(json, binary, primitive.attributes.POSITION, 3)
      const indices = primitive.indices === undefined
        ? null
        : accessorView(json, binary, primitive.indices, 1)
      const count = indices?.accessor.count ?? position.accessor.count
      const vertexAt = (index) => {
        const vertex = indices ? indices.get(index) : index
        return [position.get(vertex, 0), position.get(vertex, 1), position.get(vertex, 2)]
      }
      for (let index = 0; index + 2 < count; index += 3) {
        triangles.push([vertexAt(index), vertexAt(index + 1), vertexAt(index + 2)])
      }
    }
  }
  return triangles
}

function include(bounds, x, y, z) {
  bounds.min[0] = Math.min(bounds.min[0], x)
  bounds.min[1] = Math.min(bounds.min[1], y)
  bounds.min[2] = Math.min(bounds.min[2], z)
  bounds.max[0] = Math.max(bounds.max[0], x)
  bounds.max[1] = Math.max(bounds.max[1], y)
  bounds.max[2] = Math.max(bounds.max[2], z)
}

function intersectsTriangle(bounds, points) {
  const xs = points.map((point) => point[0])
  const ys = points.map((point) => point[1])
  return Math.max(...xs) >= bounds.west
    && Math.min(...xs) <= bounds.east
    && Math.max(...ys) >= bounds.south
    && Math.min(...ys) <= bounds.north
}

function typedBuffer(values, componentType) {
  const array = componentType === UNSIGNED_SHORT
    ? Uint16Array.from(values)
    : componentType === UNSIGNED_INT
      ? Uint32Array.from(values)
      : Float32Array.from(values)
  return Buffer.from(array.buffer, array.byteOffset, array.byteLength)
}

export function sliceGlbToBounds(input, focusBounds) {
  const { json, binary } = parseGlb(input)
  const primitive = json.meshes?.[0]?.primitives?.[0]
  if (!primitive || (primitive.mode ?? 4) !== 4 || json.meshes.length !== 1) {
    throw new Error('Focused building tiles require one triangle-list mesh')
  }
  const position = accessorView(json, binary, primitive.attributes.POSITION, 3)
  const uv = accessorView(json, binary, primitive.attributes.TEXCOORD_0, 2)
  const indices = accessorView(json, binary, primitive.indices, 1)
  const remap = new Map()
  const outputIndices = []
  const outputPositions = []
  const outputUvs = []
  const outputBounds = {
    min: [Infinity, Infinity, Infinity],
    max: [-Infinity, -Infinity, -Infinity],
  }

  const copyVertex = (sourceIndex) => {
    const existing = remap.get(sourceIndex)
    if (existing !== undefined) return existing
    const targetIndex = remap.size
    const x = position.get(sourceIndex, 0)
    const y = position.get(sourceIndex, 1)
    const z = position.get(sourceIndex, 2)
    outputPositions.push(x, y, z)
    outputUvs.push(uv.get(sourceIndex, 0), uv.get(sourceIndex, 1))
    include(outputBounds, x, y, z)
    remap.set(sourceIndex, targetIndex)
    return targetIndex
  }

  for (let index = 0; index < indices.accessor.count; index += 3) {
    const triangle = [indices.get(index), indices.get(index + 1), indices.get(index + 2)]
    const points = triangle.map((vertex) => [
      position.get(vertex, 0),
      position.get(vertex, 1),
      position.get(vertex, 2),
    ])
    if (!intersectsTriangle(focusBounds, points)) continue
    outputIndices.push(...triangle.map(copyVertex))
  }
  if (outputIndices.length === 0) return null

  const indexComponentType = remap.size <= 65_535 ? UNSIGNED_SHORT : UNSIGNED_INT
  const chunks = [
    typedBuffer(outputIndices, indexComponentType),
    typedBuffer(outputPositions, FLOAT),
    typedBuffer(outputUvs, FLOAT),
  ]
  const image = json.images?.[0]
  if (!image || image.bufferView == null) throw new Error('Building GLB has no embedded image')
  const imageView = json.bufferViews[image.bufferView]
  chunks.push(binary.subarray(
    imageView.byteOffset ?? 0,
    (imageView.byteOffset ?? 0) + imageView.byteLength,
  ))

  const bufferViews = []
  let byteOffset = 0
  const binaryParts = []
  for (const [index, chunk] of chunks.entries()) {
    const alignedOffset = align4(byteOffset)
    if (alignedOffset > byteOffset) binaryParts.push(Buffer.alloc(alignedOffset - byteOffset))
    binaryParts.push(chunk)
    bufferViews.push({
      buffer: 0,
      byteOffset: alignedOffset,
      byteLength: chunk.length,
      ...(index === 0 ? { target: 34963 } : index < 3 ? { target: 34962 } : {}),
    })
    byteOffset = alignedOffset + chunk.length
  }
  const outputBinary = Buffer.concat(binaryParts)
  const outputJson = {
    asset: json.asset,
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{
      attributes: { POSITION: 1, TEXCOORD_0: 2 },
      indices: 0,
      material: 0,
      mode: 4,
    }] }],
    materials: json.materials,
    textures: json.textures,
    samplers: json.samplers,
    images: [{ ...image, bufferView: 3 }],
    accessors: [
      {
        bufferView: 0,
        componentType: indexComponentType,
        count: outputIndices.length,
        type: 'SCALAR',
        min: [0],
        max: [remap.size - 1],
      },
      {
        bufferView: 1,
        componentType: FLOAT,
        count: remap.size,
        type: 'VEC3',
        min: outputBounds.min,
        max: outputBounds.max,
      },
      {
        bufferView: 2,
        componentType: FLOAT,
        count: remap.size,
        type: 'VEC2',
      },
    ],
    bufferViews,
    buffers: [{ byteLength: outputBinary.length }],
  }
  return {
    glb: encodeGlb(outputJson, outputBinary),
    bounds: outputBounds,
    triangleCount: outputIndices.length / 3,
  }
}

function boxFromBounds({ min, max }) {
  const center = min.map((value, index) => (value + max[index]) / 2)
  const half = min.map((value, index) => (max[index] - value) / 2)
  return [center[0], center[1], center[2], half[0], 0, 0, 0, half[1], 0, 0, 0, Math.max(half[2], 0.5)]
}

function tileBoxIntersectsFocus(child, focusBounds) {
  const box = child?.boundingVolume?.box
  if (!Array.isArray(box) || box.length !== 12) return true
  const extentX = Math.abs(box[3]) + Math.abs(box[6]) + Math.abs(box[9])
  const extentY = Math.abs(box[4]) + Math.abs(box[7]) + Math.abs(box[10])
  return box[0] + extentX >= focusBounds.west
    && box[0] - extentX <= focusBounds.east
    && box[1] + extentY >= focusBounds.south
    && box[1] - extentY <= focusBounds.north
}

export async function buildFocusedTileset({
  sourceDirectory,
  outputDirectory,
  centerWgs84,
  radiusMeters = 2_500,
}) {
  const tileset = JSON.parse(await readFile(path.join(sourceDirectory, 'tileset.json'), 'utf8'))
  const rootTransform = tileset.root.transform
  const centerPlane = projectBd09ToWebMercator(wgs84ToBd09(...centerWgs84))
  const centerLocal = [centerPlane[0] - rootTransform[12], centerPlane[1] - rootTransform[13]]
  const focusBounds = {
    west: centerLocal[0] - radiusMeters,
    east: centerLocal[0] + radiusMeters,
    south: centerLocal[1] - radiusMeters,
    north: centerLocal[1] + radiusMeters,
  }
  const childrenByUri = new Map(tileset.root.children.map((child) => [child.content?.uri, child]))
  const tileDirectory = path.join(sourceDirectory, 'tiles')
  const files = (await readdir(tileDirectory)).filter((name) => name.endsWith('.glb')).sort()
  const outputChildren = []
  const rootBounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] }
  let triangleCount = 0
  let sourceBytes = 0
  let outputBytes = 0
  await mkdir(path.join(outputDirectory, 'tiles'), { recursive: true })

  for (const file of files) {
    const uri = `tiles/${file}`
    const sourceChild = childrenByUri.get(uri)
    if (sourceChild && !tileBoxIntersectsFocus(sourceChild, focusBounds)) continue
    const input = await readFile(path.join(tileDirectory, file))
    sourceBytes += input.length
    const result = sliceGlbToBounds(input, focusBounds)
    if (!result) continue
    await writeFile(path.join(outputDirectory, uri), result.glb)
    outputChildren.push({
      id: sourceChild?.id ?? path.basename(file, '.glb'),
      boundingVolume: { box: boxFromBounds(result.bounds) },
      geometricError: 0,
      refine: 'REPLACE',
      content: { uri },
    })
    result.bounds.min.forEach((value, index) => {
      rootBounds.min[index] = Math.min(rootBounds.min[index], value)
      rootBounds.max[index] = Math.max(rootBounds.max[index], result.bounds.max[index])
    })
    triangleCount += result.triangleCount
    outputBytes += result.glb.length
  }
  const outputTileset = {
    asset: tileset.asset,
    geometricError: Math.max(radiusMeters * 2, 1),
    root: {
      boundingVolume: { box: boxFromBounds(rootBounds) },
      geometricError: Math.max(radiusMeters * 2, 1),
      refine: 'REPLACE',
      transform: rootTransform,
      children: outputChildren,
    },
  }
  const manifest = {
    source: path.basename(sourceDirectory),
    center_wgs84: centerWgs84,
    radius_m: radiusMeters,
    source_tile_count: files.length,
    output_tile_count: outputChildren.length,
    triangle_count: triangleCount,
    source_bytes: sourceBytes,
    output_bytes: outputBytes,
  }
  await Promise.all([
    writeFile(path.join(outputDirectory, 'tileset.json'), `${JSON.stringify(outputTileset, null, 2)}\n`),
    writeFile(path.join(outputDirectory, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`),
  ])
  return { tileset: outputTileset, manifest }
}

async function main() {
  const sourceDirectory = path.resolve(process.argv[2] ?? 'public/3dtiles/xiongan-webmercator')
  const outputDirectory = path.resolve(process.argv[3] ?? 'public/3dtiles/xiongan-webmercator-demo_2')
  const roads = JSON.parse(await readFile(
    path.resolve(process.argv[4] ?? 'public/showcase-data/demo_2.roads.wgs84.geojson'),
    'utf8',
  ))
  const center = roads.metadata.center
  const radiusMeters = Number(process.argv[5] ?? 2_500)
  const result = await buildFocusedTileset({
    sourceDirectory,
    outputDirectory,
    centerWgs84: [center.longitude, center.latitude],
    radiusMeters,
  })
  console.log(JSON.stringify(result.manifest))
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main()
}
