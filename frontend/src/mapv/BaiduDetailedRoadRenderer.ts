import type { Engine } from '@baidumap/mapv-three'
import * as THREE from 'three'
import type { MapGeoJsonResponse } from '../types/map'
import {
  buildDetailedRoadData,
  excludeRoadCore,
  type RoadCoordinateProjector,
  type RoadExclusionZone,
  type RoadLineFeature,
  type RoadSurfaceFeature,
} from './roadGeometry'
import {
  ROAD_ASPHALT_COLOR,
  ROAD_JUNCTION_COLOR,
  ROAD_SECONDARY_ASPHALT_CSS,
  ROAD_SIDEWALK_COLOR,
  createAsphaltMaterial,
} from './roadAppearance'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'

interface RoadMaterials {
  asphalt: THREE.MeshStandardMaterial
  secondaryAsphalt: THREE.MeshStandardMaterial
  junction: THREE.MeshStandardMaterial
  sidewalk: THREE.MeshStandardMaterial
  white: THREE.MeshStandardMaterial
  yellow: THREE.MeshStandardMaterial
}

type ScenePoint = [number, number, number]

const ASPHALT_UV_METERS = 8
const SOLID_MARKING_WIDTH_METERS = 0.12
const MEDIAN_MARKING_WIDTH_METERS = 0.14
const DASH_LENGTH_METERS = 3
const DASH_PITCH_METERS = 8

function colorNumber(css: string): number {
  return Number.parseInt(css.replace('#', ''), 16)
}

function seedFrom(value: string): number {
  return value.split('').reduce((sum, character) => sum + character.charCodeAt(0), 1)
}

function disposeMaterial(material: THREE.Material): void {
  if ('map' in material) (material.map as THREE.Texture | null)?.dispose()
  material.dispose()
}

export class BaiduDetailedRoadRenderer {
  private readonly group = new THREE.Group()
  private currentKey: string | null = null
  private response: MapGeoJsonResponse | null = null
  private exclusionZone: RoadExclusionZone | null = null
  private materials: RoadMaterials | null = null
  private materialKey: string | null = null
  private anchor: ScenePoint = [0, 0, 0]
  private horizontalScale = 1

  constructor(
    private readonly engine: Engine,
    private readonly projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
  ) {
    this.group.name = 'sumo-asphalt-road-network'
    this.group.renderOrder = 10
    this.engine.add(this.group)
  }

  render(response: MapGeoJsonResponse | null): void {
    this.response = response
    if (!response) {
      this.clear()
      return
    }
    const metadata = response.geojson?.metadata ?? {}
    const dataVersion = String(metadata.generated_at ?? metadata.data_version ?? metadata.vertex_count ?? '')
    const zoneKey = this.exclusionZone
      ? `${this.exclusionZone.center.join(':')}:${this.exclusionZone.radiusMeters}`
      : 'none'
    const nextKey = `${response.intersection_id}:${response.radius_m}:${dataVersion}:${zoneKey}`
    if (nextKey === this.currentKey) return

    this.clearGeometry()
    this.ensureMaterials(response.intersection_id)
    this.horizontalScale = 1 / Math.max(0.4, Math.cos(response.center.latitude * Math.PI / 180))
    const projectedOrigin = this.projector([response.center.longitude, response.center.latitude, 0])
    const sceneOrigin = this.engine.map.projectArrayCoordinate([
      projectedOrigin[0],
      projectedOrigin[1],
      projectedOrigin[2] ?? 0,
    ])
    this.anchor = [sceneOrigin[0], sceneOrigin[1], sceneOrigin[2] ?? 0]
    this.group.position.set(...this.anchor)

    const data = buildDetailedRoadData(excludeRoadCore(response, this.exclusionZone), this.projector)
    this.addSurfaces(data.shoulders, this.materials!.sidewalk, 9)
    this.addSurfaces(data.mainSurfaces, this.materials!.asphalt, 10, true)
    this.addSurfaces(data.secondarySurfaces, this.materials!.secondaryAsphalt, 10, true)
    this.addSurfaces(data.junctionSurfaces, this.materials!.junction, 11, true)
    this.addLines(data.outerBoundaries, this.materials!.white, SOLID_MARKING_WIDTH_METERS, false, 16)
    this.addLines(data.medians, this.materials!.yellow, MEDIAN_MARKING_WIDTH_METERS, false, 17)
    this.addLines(data.laneDividers, this.materials!.white, SOLID_MARKING_WIDTH_METERS, true, 17)
    this.addSurfaces(data.stopLines, this.materials!.white, 18)
    this.addSurfaces(data.crosswalkStripes, this.materials!.white, 19)
    this.currentKey = nextKey
    this.engine.requestRender()
  }

  setRealisticDetailActive(active: boolean, zone: RoadExclusionZone | null = null): void {
    const nextZone = active ? zone : null
    const unchanged = this.exclusionZone?.center[0] === nextZone?.center[0]
      && this.exclusionZone?.center[1] === nextZone?.center[1]
      && this.exclusionZone?.radiusMeters === nextZone?.radiusMeters
    if (unchanged) return
    this.exclusionZone = nextZone
    this.currentKey = null
    this.render(this.response)
  }

  clear(): void {
    this.currentKey = null
    this.clearGeometry()
    this.engine.requestRender()
  }

  destroy(): void {
    this.clearGeometry()
    this.disposeMaterials()
    this.engine.remove(this.group)
  }

  private ensureMaterials(intersectionId: string): void {
    if (this.materials && this.materialKey === intersectionId) return
    this.disposeMaterials()
    const seed = seedFrom(intersectionId)
    const marking = (color: number) => new THREE.MeshStandardMaterial({
      color,
      roughness: 0.58,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    })
    this.materials = {
      asphalt: createAsphaltMaterial(seed, ROAD_ASPHALT_COLOR),
      secondaryAsphalt: createAsphaltMaterial(seed + 97, colorNumber(ROAD_SECONDARY_ASPHALT_CSS)),
      junction: createAsphaltMaterial(seed + 193, ROAD_JUNCTION_COLOR),
      sidewalk: new THREE.MeshStandardMaterial({ color: ROAD_SIDEWALK_COLOR, roughness: 0.92 }),
      white: marking(0xf0eee3),
      yellow: marking(0xe1b63c),
    }
    this.materialKey = intersectionId
  }

  private disposeMaterials(): void {
    if (!this.materials) return
    for (const material of Object.values(this.materials)) disposeMaterial(material)
    this.materials = null
    this.materialKey = null
  }

  private clearGeometry(): void {
    for (const child of [...this.group.children]) {
      this.group.remove(child)
      if (child instanceof THREE.Mesh) child.geometry.dispose()
    }
  }

  private projectLocal(coordinate: readonly number[]): ScenePoint {
    const scene = this.engine.map.projectArrayCoordinate([
      coordinate[0],
      coordinate[1],
      coordinate[2] ?? 0,
    ])
    return [
      scene[0] - this.anchor[0],
      scene[1] - this.anchor[1],
      (scene[2] ?? 0) - this.anchor[2],
    ]
  }

  private polygonGeometry(feature: RoadSurfaceFeature, textured: boolean): THREE.BufferGeometry | null {
    const ring = feature.geometry.coordinates[0]
      .slice(0, -1)
      .map((coordinate) => this.projectLocal(coordinate))
    if (ring.length < 3) return null
    const shape = new THREE.Shape(ring.map(([x, y]) => new THREE.Vector2(x, y)))
    const geometry = new THREE.ShapeGeometry(shape)
    const averageZ = ring.reduce((sum, point) => sum + point[2], 0) / ring.length
    geometry.translate(0, 0, averageZ)
    if (textured) {
      const positions = geometry.getAttribute('position')
      const uvs = geometry.getAttribute('uv')
      const scale = ASPHALT_UV_METERS * this.horizontalScale
      for (let index = 0; index < positions.count; index += 1) {
        uvs.setXY(index, positions.getX(index) / scale, positions.getY(index) / scale)
      }
      uvs.needsUpdate = true
    }
    geometry.computeVertexNormals()
    return geometry
  }

  private addSurfaces(
    features: RoadSurfaceFeature[],
    material: THREE.Material,
    renderOrder: number,
    textured = false,
  ): void {
    for (const feature of features) {
      const geometry = this.polygonGeometry(feature, textured)
      if (!geometry) continue
      const mesh = new THREE.Mesh(geometry, material)
      mesh.renderOrder = renderOrder
      this.group.add(mesh)
    }
  }

  private addLines(
    features: RoadLineFeature[],
    material: THREE.Material,
    widthMeters: number,
    dashed: boolean,
    renderOrder: number,
  ): void {
    for (const feature of features) {
      const points = feature.geometry.coordinates.map((coordinate) => this.projectLocal(coordinate))
      const geometry = this.lineGeometry(points, widthMeters * this.horizontalScale, dashed)
      if (!geometry) continue
      const mesh = new THREE.Mesh(geometry, material)
      mesh.renderOrder = renderOrder
      this.group.add(mesh)
    }
  }

  private lineGeometry(points: ScenePoint[], width: number, dashed: boolean): THREE.BufferGeometry | null {
    const positions: number[] = []
    const indices: number[] = []
    const addQuad = (start: ScenePoint, end: ScenePoint) => {
      const dx = end[0] - start[0]
      const dy = end[1] - start[1]
      const length = Math.hypot(dx, dy)
      if (length < 0.01) return
      const nx = -dy / length * width / 2
      const ny = dx / length * width / 2
      const base = positions.length / 3
      positions.push(
        start[0] + nx, start[1] + ny, start[2],
        start[0] - nx, start[1] - ny, start[2],
        end[0] + nx, end[1] + ny, end[2],
        end[0] - nx, end[1] - ny, end[2],
      )
      indices.push(base, base + 1, base + 2, base + 1, base + 3, base + 2)
    }

    for (let index = 0; index < points.length - 1; index += 1) {
      const start = points[index]
      const end = points[index + 1]
      if (!dashed) {
        addQuad(start, end)
        continue
      }
      const dx = end[0] - start[0]
      const dy = end[1] - start[1]
      const dz = end[2] - start[2]
      const length = Math.hypot(dx, dy)
      const pitch = DASH_PITCH_METERS * this.horizontalScale
      const dash = DASH_LENGTH_METERS * this.horizontalScale
      for (let offset = 0; offset < length; offset += pitch) {
        const from = offset / length
        const to = Math.min(length, offset + dash) / length
        addQuad(
          [start[0] + dx * from, start[1] + dy * from, start[2] + dz * from],
          [start[0] + dx * to, start[1] + dy * to, start[2] + dz * to],
        )
      }
    }
    if (positions.length === 0) return null
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    geometry.setIndex(indices)
    geometry.computeVertexNormals()
    return geometry
  }
}
