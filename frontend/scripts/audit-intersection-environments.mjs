import { access, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const publicDirectory = path.join(frontendDirectory, 'public')
const INTERSECTION_COUNT = 20
const ROAD_TRANSITION_OVERLAP_METERS = 22

async function readJson(file) {
  return JSON.parse(await readFile(file, 'utf8'))
}

function publicUrlPath(url) {
  if (typeof url !== 'string' || !url.startsWith('/')) return null
  return path.join(publicDirectory, ...url.slice(1).split('/'))
}

function buildingCoverage(triangleCount) {
  if (triangleCount >= 8_000) return 'dense'
  if (triangleCount >= 3_000) return 'moderate'
  return 'sparse-source'
}

function edgeRadiusMeters(edge, horizontalScale) {
  const points = edge.lanes.flatMap((lane) => lane.renderPoints ?? lane.points)
  return Math.max(...points.map((point) => Math.hypot(point[0], point[1]))) / horizontalScale
}

async function missingFiles(paths) {
  const missing = []
  await Promise.all(paths.map(async (file) => {
    try {
      await access(file)
    } catch {
      missing.push(file)
    }
  }))
  return missing.sort()
}

export async function auditIntersectionEnvironments() {
  const rows = []
  for (let index = 1; index <= INTERSECTION_COUNT; index += 1) {
    const intersectionId = `demo_${index}`
    const bundleDirectory = path.join(publicDirectory, 'intersections', 'v3', intersectionId)
    const tilesDirectory = path.join(publicDirectory, '3dtiles', 'intersections', intersectionId)
    const [scene, environment, green, water, facilities, tileset, building] = await Promise.all([
      readJson(path.join(bundleDirectory, 'manifest.json')),
      readJson(path.join(bundleDirectory, 'environment.json')),
      readJson(path.join(bundleDirectory, 'green.geojson')),
      readJson(path.join(bundleDirectory, 'water.geojson')),
      readJson(path.join(bundleDirectory, 'facilities.json')),
      readJson(path.join(tilesDirectory, 'tileset.json')),
      readJson(path.join(tilesDirectory, 'manifest.json')),
    ])
    const errors = []
    const warnings = []
    const children = tileset.root?.children ?? []
    const tileFiles = children
      .map((child) => child.content?.uri)
      .filter((uri) => typeof uri === 'string')
      .map((uri) => path.join(tilesDirectory, ...uri.split('/')))
    const assetFiles = [
      publicUrlPath(environment.buildingTilesetUrl),
      publicUrlPath(environment.facilitiesUrl),
      publicUrlPath(environment.streetlight?.modelUrl),
      publicUrlPath(environment.geojson?.green),
      publicUrlPath(environment.geojson?.water),
      ...tileFiles,
    ].filter(Boolean)
    const missing = await missingFiles(assetFiles)
    if (missing.length) errors.push(`missing ${missing.length} referenced assets`)
    if (children.length !== building.output_tile_count) {
      errors.push(`tileset children ${children.length} != manifest ${building.output_tile_count}`)
    }
    if (green.features.some((feature) => feature.properties?.kind === 'green-ground')) {
      errors.push('synthetic circular green ground is still present')
    }
    if (environment.geojson?.water !== `/intersections/v3/${intersectionId}/water.geojson`) {
      errors.push('intersection water layer is not configured')
    }
    const radii = scene.edges.map((edge) => edgeRadiusMeters(edge, scene.horizontalScale))
    const boundaryRadii = radii.filter((radius) => radius >= scene.radiusMeters - 15)
    const minimumBoundaryOverlap = boundaryRadii.length
      ? Math.min(...boundaryRadii.map((radius) => (
        radius - (scene.radiusMeters - ROAD_TRANSITION_OVERLAP_METERS)
      )))
      : 0
    if (boundaryRadii.length < 2) errors.push('fewer than two roads reach the scene boundary')
    if (minimumBoundaryOverlap < 4) errors.push('road transition overlap is below 4 meters')
    else if (minimumBoundaryOverlap < 15) warnings.push('local road transition is below the 15 meter visual target; Baidu base road is required')
    const coverage = buildingCoverage(building.triangle_count)
    if (coverage === 'sparse-source') warnings.push('local building source is sparse; Baidu building fallback stays disabled')
    if (green.features.length === 0) warnings.push('no local OSM green polygon; use Baidu green base layer')
    if (water.features.length === 0) warnings.push('no local OSM water polygon; use Baidu water base layer')
    rows.push({
      intersectionId,
      status: errors.length ? 'error' : warnings.length ? 'warning' : 'ready',
      buildings: {
        coverage,
        tiles: building.output_tile_count,
        triangles: building.triangle_count,
        bytes: building.output_bytes,
      },
      landcover: {
        greenFeatures: green.features.length,
        waterFeatures: water.features.length,
        fallback: 'Baidu vector base map',
      },
      facilities: {
        lamps: facilities.lamps.length,
        cameras: facilities.cameras.length,
      },
      roads: {
        boundaryEdges: boundaryRadii.length,
        minimumBoundaryOverlapMeters: Number(minimumBoundaryOverlap.toFixed(1)),
        frontendPatchVisible: boundaryRadii.length >= 2,
        continuityClassification: minimumBoundaryOverlap >= 15
          ? 'local-overlap'
          : 'baidu-base-dependent',
      },
      errors,
      warnings,
    })
  }
  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    reference: 'huiyan-fe/mapv-three-showcases/src/pages/yizhuang',
    policy: {
      buildings: 'one global local 3D Tiles source; Baidu native buildings disabled; sparse source areas are never fabricated',
      landcover: 'local OSM polygons over the global Baidu green/water base',
      roads: `all local patches stay visible; Baidu continuous road base bridges patches; ${ROAD_TRANSITION_OVERLAP_METERS}m generation target and 15m visual warning threshold`,
    },
    intersections: rows,
    summary: {
      ready: rows.filter((row) => row.status === 'ready').length,
      warning: rows.filter((row) => row.status === 'warning').length,
      error: rows.filter((row) => row.status === 'error').length,
      sparseBuildingSources: rows.filter((row) => row.buildings.coverage === 'sparse-source')
        .map((row) => row.intersectionId),
      baiduRoadDependent: rows.filter((row) => row.roads.continuityClassification === 'baidu-base-dependent')
        .map((row) => row.intersectionId),
    },
  }
}

async function main() {
  const report = await auditIntersectionEnvironments()
  console.log('intersection\tstatus\ttiles\ttriangles\tgreen\twater\tlamps\troad-overlap-m')
  for (const row of report.intersections) {
    console.log([
      row.intersectionId,
      row.status,
      row.buildings.tiles,
      row.buildings.triangles,
      row.landcover.greenFeatures,
      row.landcover.waterFeatures,
      row.facilities.lamps,
      row.roads.minimumBoundaryOverlapMeters,
    ].join('\t'))
  }
  console.log(JSON.stringify(report.summary))
  const outputArgument = process.argv.find((argument) => argument.startsWith('--output='))
  if (outputArgument) {
    const output = path.resolve(frontendDirectory, outputArgument.slice('--output='.length))
    await mkdir(path.dirname(output), { recursive: true })
    await writeFile(output, `${JSON.stringify(report, null, 2)}\n`)
    console.log(`Wrote ${output}`)
  }
  if (report.summary.error > 0) process.exitCode = 1
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  await main()
}
