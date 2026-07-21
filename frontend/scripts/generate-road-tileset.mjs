import { createHash } from 'node:crypto'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import {
  Box3,
  BufferGeometry,
  Color,
  Float32BufferAttribute,
  Group,
  Mesh,
  MeshStandardMaterial,
  Shape,
  ShapeGeometry,
  Vector2,
  Vector3,
} from 'three'
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'

import { buildDetailedRoadData } from '../src/mapv/roadGeometry.ts'
import {
  projectBd09ToWebMercator,
  wgs84ToBd09,
} from '../src/mapv/sceneCoordinates.ts'

class NodeFileReader {
  result = null
  onloadend = null
  onerror = null

  readAsArrayBuffer(blob) {
    void blob.arrayBuffer().then((result) => {
      this.result = result
      this.onloadend?.()
    }, (error) => this.onerror?.(error))
  }

  readAsDataURL(blob) {
    void blob.arrayBuffer().then((result) => {
      this.result = `data:${blob.type};base64,${Buffer.from(result).toString('base64')}`
      this.onloadend?.()
    }, (error) => this.onerror?.(error))
  }
}

if (typeof globalThis.FileReader === 'undefined') globalThis.FileReader = NodeFileReader

export function createLocalBaiduPlaneProjector(center) {
  const origin = projectBd09ToWebMercator(wgs84ToBd09(center.longitude, center.latitude))
  return ([longitude, latitude, height]) => {
    const projected = projectBd09ToWebMercator(wgs84ToBd09(longitude, latitude))
    return [projected[0] - origin[0], projected[1] - origin[1], height]
  }
}

function material(name, color) {
  const value = new MeshStandardMaterial({
    color: new Color(color),
    metalness: 0,
    roughness: 0.96,
  })
  value.name = name
  return value
}

function polygonGeometry(feature) {
  const ring = feature.geometry.coordinates[0]
  const points = ring.slice(0, -1).map(([x, y]) => new Vector2(x, y))
  if (points.length < 3) return null
  const geometry = new ShapeGeometry(new Shape(points))
  geometry.translate(0, 0, Number(ring[0][2] ?? 0))
  return geometry
}

function segmentGeometry(start, end, width) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const length = Math.hypot(dx, dy)
  if (length < 0.01) return null
  const nx = -dy / length * width / 2
  const ny = dx / length * width / 2
  const z1 = Number(start[2] ?? 0)
  const z2 = Number(end[2] ?? z1)
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute([
    start[0] + nx, start[1] + ny, z1,
    start[0] - nx, start[1] - ny, z1,
    end[0] - nx, end[1] - ny, z2,
    end[0] + nx, end[1] + ny, z2,
  ], 3))
  geometry.setIndex([0, 1, 2, 0, 2, 3])
  geometry.computeVertexNormals()
  return geometry
}

function lineGeometries(features, width, dashed = false) {
  const geometries = []
  for (const feature of features) {
    const coordinates = feature.geometry.coordinates
    for (let index = 0; index < coordinates.length - 1; index += 1) {
      const start = coordinates[index]
      const end = coordinates[index + 1]
      const dx = end[0] - start[0]
      const dy = end[1] - start[1]
      const length = Math.hypot(dx, dy)
      if (!dashed) {
        const geometry = segmentGeometry(start, end, width)
        if (geometry) geometries.push(geometry)
        continue
      }
      for (let offset = 0; offset < length; offset += 7) {
        const from = offset / length
        const to = Math.min(offset + 3, length) / length
        const geometry = segmentGeometry(
          [start[0] + dx * from, start[1] + dy * from, start[2]],
          [start[0] + dx * to, start[1] + dy * to, end[2]],
          width,
        )
        if (geometry) geometries.push(geometry)
      }
    }
  }
  return geometries
}

function addMesh(group, name, geometries, meshMaterial) {
  const valid = geometries.filter(Boolean)
  if (valid.length === 0) return
  const geometry = mergeGeometries(valid)
  const mesh = new Mesh(geometry, meshMaterial)
  mesh.name = name
  group.add(mesh)
}

function boundingVolume(group) {
  const box = new Box3().setFromObject(group)
  const center = box.getCenter(new Vector3())
  const size = box.getSize(new Vector3()).multiplyScalar(0.5)
  return [
    center.x, center.y, center.z,
    size.x, 0, 0,
    0, size.y, 0,
    0, 0, Math.max(size.z, 0.5),
  ]
}

function webMercatorTransform([longitude, latitude]) {
  const [x, y] = projectBd09ToWebMercator([longitude, latitude])
  return [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    x, y, 0, 1,
  ]
}

async function exportGlb(group) {
  const result = await new GLTFExporter().parseAsync(group, {
    binary: true,
    onlyVisible: true,
  })
  return Buffer.from(result)
}

export async function buildRoadTileset(response) {
  const placementBd09 = wgs84ToBd09(response.center.longitude, response.center.latitude)
  const data = buildDetailedRoadData(response, createLocalBaiduPlaneProjector(response.center))
  const group = new Group()
  group.name = `${response.intersection_id}-roads`

  addMesh(group, 'road-shoulders', data.shoulders.map(polygonGeometry), material('road-shoulder', '#292d31'))
  addMesh(group, 'road-main', data.mainSurfaces.map(polygonGeometry), material('road-main', '#3b4046'))
  addMesh(group, 'road-secondary', data.secondarySurfaces.map(polygonGeometry), material('road-secondary', '#454b52'))
  addMesh(group, 'road-junctions', data.junctionSurfaces.map(polygonGeometry), material('road-junction', '#3b4046'))
  addMesh(group, 'road-boundaries', lineGeometries(data.outerBoundaries, 0.16), material('road-white', '#f3f1df'))
  addMesh(group, 'road-lane-dividers', lineGeometries(data.laneDividers, 0.13, true), material('road-divider', '#f3f1df'))
  addMesh(group, 'road-medians', lineGeometries(data.medians, 0.18), material('road-median', '#e5b94b'))
  addMesh(group, 'road-stop-lines', data.stopLines.map(polygonGeometry), material('road-stop', '#f3f1df'))
  addMesh(group, 'road-crosswalks', data.crosswalkStripes.map(polygonGeometry), material('road-crosswalk', '#f3f1df'))

  const box = boundingVolume(group)
  const glb = await exportGlb(group)
  const metadata = response.geojson.metadata ?? {}
  const sourceSha256 = createHash('sha256')
    .update(JSON.stringify(response.geojson))
    .digest('hex')
  const tileset = {
    asset: { version: '1.1', gltfUpAxis: 'Z' },
    geometricError: Math.max(box[3], box[7]) * 2,
    root: {
      transform: webMercatorTransform(placementBd09),
      boundingVolume: { box },
      geometricError: 0,
      refine: 'ADD',
      content: { uri: 'tiles/road.glb' },
    },
  }
  const manifest = {
    intersection_id: response.intersection_id,
    placement_mode: 'actual',
    placement_bd09: placementBd09,
    radius_m: response.radius_m,
    source_generated_at: metadata.generated_at ?? null,
    source_vertex_count: metadata.vertex_count ?? null,
    feature_count: response.geojson.features.filter(
      (feature) => feature.geometry?.type === 'LineString',
    ).length,
    source_sha256: sourceSha256,
    coordinate_system: 'LOCAL_BD09_WEB_MERCATOR_METERS',
    origin_wgs84: [response.center.longitude, response.center.latitude],
  }
  return { glb, tileset, manifest }
}

function geoJsonBounds(features) {
  const bounds = [Infinity, Infinity, -Infinity, -Infinity]
  const visit = (value) => {
    if (!Array.isArray(value)) return
    if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
      bounds[0] = Math.min(bounds[0], value[0])
      bounds[1] = Math.min(bounds[1], value[1])
      bounds[2] = Math.max(bounds[2], value[0])
      bounds[3] = Math.max(bounds[3], value[1])
      return
    }
    value.forEach(visit)
  }
  features.forEach((feature) => visit(feature.geometry?.coordinates))
  return bounds
}

async function main() {
  const sourcePath = path.resolve(
    process.argv[2] ?? '../data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson',
  )
  const source = JSON.parse(await readFile(sourcePath, 'utf8'))
  const [west, south, east, north] = geoJsonBounds(source.features)
  const response = {
    intersection_id: source.metadata.intersection_id,
    center: source.metadata.center,
    radius_m: source.metadata.radius_m,
    bounds: { west, south, east, north },
    geojson: source,
  }
  const outputDirectory = path.resolve(
    process.argv[3] ?? `public/3dtiles/roads/${response.intersection_id}`,
  )
  const result = await buildRoadTileset(response)
  await mkdir(path.join(outputDirectory, 'tiles'), { recursive: true })
  await Promise.all([
    writeFile(path.join(outputDirectory, 'tiles', 'road.glb'), result.glb),
    writeFile(path.join(outputDirectory, 'tileset.json'), `${JSON.stringify(result.tileset, null, 2)}\n`),
    writeFile(path.join(outputDirectory, 'manifest.json'), `${JSON.stringify(result.manifest, null, 2)}\n`),
  ])
  console.log(`Generated ${path.relative(process.cwd(), outputDirectory)} (${result.glb.length} bytes)`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main()
}
