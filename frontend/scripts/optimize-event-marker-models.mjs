import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { NodeIO } from '@gltf-transform/core'
import { ALL_EXTENSIONS, EXTTextureWebP } from '@gltf-transform/extensions'
import {
  dedup,
  flatten,
  join,
  meshopt,
  prune,
  simplify,
  weld,
} from '@gltf-transform/functions'
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer'
import sharp from 'sharp'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDirectory, '..')
const outputDirectory = path.join(frontendRoot, 'public', 'models', 'events')
const sourceDirectory = process.env.EVENT_MARKER_MODEL_DIR
  ? path.resolve(process.env.EVENT_MARKER_MODEL_DIR)
  : path.resolve(frontendRoot, '..', '..', 'model')

const MODEL_SPECS = [
  { kind: 'red', sourceName: '红色标识.glb', outputName: 'event-marker-red.glb' },
  { kind: 'yellow', sourceName: '黄色标识.glb', outputName: 'event-marker-yellow.glb' },
]

const MAX_TEXTURE_SIZE = 1024
const MAX_TRIANGLES = 60_000
const MAX_FILE_BYTES = 3 * 1024 * 1024
const SIMPLIFY_RATIO = 0.035
const SIMPLIFY_ERROR = 0.01

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function accessorBounds(document) {
  let min = [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY]
  let max = [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY]
  for (const mesh of document.getRoot().listMeshes()) {
    for (const primitive of mesh.listPrimitives()) {
      const position = primitive.getAttribute('POSITION')
      const localMin = position?.getMin([])
      const localMax = position?.getMax([])
      if (!localMin || !localMax) continue
      min = min.map((value, index) => Math.min(value, localMin[index]))
      max = max.map((value, index) => Math.max(value, localMax[index]))
    }
  }
  return { min, max }
}

function triangleCount(document) {
  let count = 0
  for (const mesh of document.getRoot().listMeshes()) {
    for (const primitive of mesh.listPrimitives()) {
      const indices = primitive.getIndices()
      const positions = primitive.getAttribute('POSITION')
      count += Math.floor((indices?.getCount() ?? positions?.getCount() ?? 0) / 3)
    }
  }
  return count
}

async function resizeTextures(document) {
  const details = []
  document.createExtension(EXTTextureWebP).setRequired(true)
  for (const texture of document.getRoot().listTextures()) {
    const image = texture.getImage()
    if (!image) continue
    const pipeline = sharp(image, { failOn: 'none' })
    const metadata = await pipeline.metadata()
    const width = metadata.width ?? 0
    const height = metadata.height ?? 0
    const scale = Math.min(1, MAX_TEXTURE_SIZE / Math.max(width, height, 1))
    const nextWidth = Math.max(1, Math.round(width * scale))
    const nextHeight = Math.max(1, Math.round(height * scale))
    const encoded = await pipeline
      .resize(nextWidth, nextHeight, { fit: 'inside', withoutEnlargement: true })
      .webp({ quality: 78, effort: 6, smartSubsample: true })
      .toBuffer()
    texture.setImage(encoded).setMimeType('image/webp')
    details.push({
      name: texture.getName(),
      sourceWidth: width,
      sourceHeight: height,
      width: nextWidth,
      height: nextHeight,
      byteLength: encoded.byteLength,
      mimeType: 'image/webp',
    })
  }
  return details
}

async function optimize(spec) {
  const sourcePath = path.join(sourceDirectory, spec.sourceName)
  const outputPath = path.join(outputDirectory, spec.outputName)
  const source = await fs.readFile(sourcePath)
  const io = new NodeIO()
    .registerExtensions(ALL_EXTENSIONS)
    .registerDependencies({
      'meshopt.decoder': MeshoptDecoder,
      'meshopt.encoder': MeshoptEncoder,
    })
  const document = await io.readBinary(source)

  await MeshoptSimplifier.ready
  await MeshoptEncoder.ready
  await document.transform(
    dedup(),
    flatten(),
    join({ keepNamed: false }),
    weld(),
    simplify({
      simplifier: MeshoptSimplifier,
      ratio: SIMPLIFY_RATIO,
      error: SIMPLIFY_ERROR,
      lockBorder: false,
    }),
    prune(),
  )
  const textures = await resizeTextures(document)
  await document.transform(meshopt({ encoder: MeshoptEncoder, level: 'high' }))
  const output = await io.writeBinary(document)
  await fs.writeFile(outputPath, output)

  const triangles = triangleCount(document)
  if (triangles > MAX_TRIANGLES) {
    throw new Error(`${spec.outputName} has ${triangles} triangles; limit is ${MAX_TRIANGLES}`)
  }
  if (output.byteLength > MAX_FILE_BYTES) {
    throw new Error(`${spec.outputName} is ${output.byteLength} bytes; limit is ${MAX_FILE_BYTES}`)
  }
  return {
    kind: spec.kind,
    sourcePath: sourcePath.replaceAll('\\', '/'),
    sourceSha256: sha256(source),
    outputUrl: `/models/events/${spec.outputName}`,
    outputSha256: sha256(output),
    sourceByteLength: source.byteLength,
    outputByteLength: output.byteLength,
    triangles,
    bounds: accessorBounds(document),
    textures,
    generatedAt: new Date().toISOString(),
    optimizer: {
      name: '@gltf-transform/functions',
      simplifyRatio: SIMPLIFY_RATIO,
      simplifyError: SIMPLIFY_ERROR,
      compression: 'meshopt-high',
      maximumTextureSize: MAX_TEXTURE_SIZE,
      textureFormat: 'webp',
    },
  }
}

await fs.mkdir(outputDirectory, { recursive: true })
const models = []
for (const spec of MODEL_SPECS) models.push(await optimize(spec))
await fs.writeFile(
  path.join(outputDirectory, 'manifest.json'),
  `${JSON.stringify({ schemaVersion: 1, models }, null, 2)}\n`,
)
for (const model of models) {
  console.log(`${model.kind}: ${(model.outputByteLength / 1024 / 1024).toFixed(2)} MiB, ${model.triangles} triangles`)
}
