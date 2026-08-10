import type { Engine } from '@baidumap/mapv-three'
import {
  DoubleSide,
  BufferGeometry,
  Group,
  InstancedMesh,
  Material,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Object3D,
  Quaternion,
  StaticDrawUsage,
  Texture,
  Vector3,
} from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { RoadCoordinateProjector } from '../roadGeometry'
import {
  parseSceneVegetationManifest,
  type SceneVegetationItem,
  type SceneVegetationManifest,
  type VegetationKind,
} from './sceneVegetation'

const KIND_MAX_CAMERA_RANGE: Record<VegetationKind, number> = {
  tree: 1_600,
  hedge: 1_250,
  bush: 1_100,
  grass: 600,
  flowers: 650,
}

const X_AXIS = new Vector3(1, 0, 0)
const Z_AXIS = new Vector3(0, 0, 1)
const GLTF_UP_TO_SCENE_UP = new Quaternion().setFromAxisAngle(X_AXIS, Math.PI / 2)

interface VegetationCell {
  group: Group
  center: [number, number]
}

export class VegetationRenderer {
  private readonly engine: Engine
  private readonly projector: RoadCoordinateProjector
  private readonly transform = new Object3D()
  private readonly placementMatrix = new Matrix4()
  private readonly relativeMatrix = new Matrix4()
  private readonly headingRotation = new Quaternion()
  private root: Group | null = null
  private sourceScene: Object3D | null = null
  private cells: VegetationCell[] = []
  private destroyed = false
  private interactionActive = false
  private visibilityTimer: ReturnType<typeof setInterval> | null = null
  private requestVersion = 0

  constructor(engine: Engine, projector: RoadCoordinateProjector) {
    this.engine = engine
    this.projector = projector
  }

  async load(manifestUrl: string, modelUrl: string): Promise<void> {
    const version = ++this.requestVersion
    const response = await fetch(manifestUrl)
    if (!response.ok) throw new Error(`Vegetation manifest returned HTTP ${response.status}`)
    const manifest = parseSceneVegetationManifest(await response.json())
    const gltf = await new GLTFLoader().loadAsync(modelUrl)
    if (this.destroyed || version !== this.requestVersion) {
      disposeObject(gltf.scene)
      return
    }
    this.clearResources()
    this.sourceScene = gltf.scene
    this.prepareMaterials(gltf.scene)
    this.build(manifest, gltf.scene)
    this.syncVisibility()
    this.visibilityTimer = setInterval(() => this.syncVisibility(), 250)
    this.engine.requestRender()
  }

  destroy(): void {
    this.destroyed = true
    this.requestVersion += 1
    this.clearResources()
  }

  clearScene(): void {
    this.requestVersion += 1
    this.clearResources()
    this.engine.requestRender()
  }

  setInteractionActive(active: boolean): void {
    if (this.interactionActive === active) return
    this.interactionActive = active
    this.syncVisibility()
  }

  private build(manifest: SceneVegetationManifest, source: Object3D): void {
    source.updateMatrixWorld(true)
    const root = new Group()
    root.name = 'scene-vegetation'
    const byCell = groupBy(manifest.items, (item) => item.cell)

    for (const [cellId, items] of byCell) {
      const cellGroup = new Group()
      cellGroup.name = `vegetation-cell-${cellId}`
      const byVariant = groupBy(items, (item) => item.variant)
      for (const [variant, variantItems] of byVariant) {
        const prototype = source.getObjectByName(variant)
        if (!prototype) {
          console.warn(`[vegetation] missing plant variant ${variant}`)
          continue
        }
        prototype.updateMatrixWorld(true)
        prototype.traverse((candidate) => {
          if (!(candidate instanceof Mesh)) return
          const mesh = this.createInstances(candidate, prototype, variantItems)
          mesh.userData.vegetationKind = variantItems[0].kind
          cellGroup.add(mesh)
        })
      }
      if (!cellGroup.children.length) continue
      root.add(cellGroup)
      this.cells.push({
        group: cellGroup,
        center: averagePosition(items.map((item) => this.projectItemPosition(item))),
      })
    }

    this.root = this.engine.add(root) as Group
  }

  private createInstances(
    template: Mesh,
    prototype: Object3D,
    items: SceneVegetationItem[],
  ): InstancedMesh {
    const mesh = new InstancedMesh(template.geometry, template.material, items.length)
    mesh.name = `${prototype.name}-${template.name}-instances`
    mesh.instanceMatrix.setUsage(StaticDrawUsage)
    this.relativeMatrix.copy(prototype.matrixWorld).invert().multiply(template.matrixWorld)
    items.forEach((item, index) => {
      const projected = this.projectItemPosition(item)
      const geographic = projected[2] == null
        ? [projected[0], projected[1]]
        : [projected[0], projected[1], projected[2]]
      const scene = this.engine.map.projectArrayCoordinate(geographic)
      this.transform.position.set(scene[0], scene[1], (scene[2] ?? 0) + 0.08)
      this.headingRotation.setFromAxisAngle(Z_AXIS, item.heading)
      this.transform.quaternion.copy(this.headingRotation).multiply(GLTF_UP_TO_SCENE_UP)
      this.transform.scale.setScalar(item.scale)
      this.transform.updateMatrix()
      this.placementMatrix.copy(this.transform.matrix).multiply(this.relativeMatrix)
      mesh.setMatrixAt(index, this.placementMatrix)
    })
    mesh.computeBoundingBox()
    mesh.computeBoundingSphere()
    mesh.frustumCulled = false
    mesh.renderOrder = 7
    return mesh
  }

  private prepareMaterials(source: Object3D): void {
    const converted = new Map<Material, Material>()
    const convert = (material: Material): Material => {
      const existing = converted.get(material)
      if (existing) return existing
      if (!(material instanceof MeshStandardMaterial)) {
        material.transparent = false
        material.alphaTest = 0.32
        material.depthWrite = true
        material.side = DoubleSide
        material.needsUpdate = true
        converted.set(material, material)
        return material
      }
      const basic = new MeshBasicMaterial({
        name: material.name,
        color: material.color,
        map: material.map,
        alphaMap: material.alphaMap,
        alphaTest: 0.32,
        side: DoubleSide,
        transparent: false,
        depthWrite: true,
        toneMapped: true,
      })
      converted.set(material, basic)
      return basic
    }

    source.traverse((candidate) => {
      if (!(candidate instanceof Mesh)) return
      candidate.material = Array.isArray(candidate.material)
        ? candidate.material.map(convert)
        : convert(candidate.material)
    })
    for (const [original, replacement] of converted) {
      if (original !== replacement) original.dispose()
    }
  }

  private projectItemPosition(item: SceneVegetationItem): [number, number, number?] {
    return this.projector(item.position[2] == null
      ? [item.position[0], item.position[1]]
      : [item.position[0], item.position[1], item.position[2]])
  }

  private syncVisibility(): void {
    const center = this.engine.map.getCenter()
    const cameraRange = this.engine.map.getRange()
    if (!Array.isArray(center) || center.length < 2) return
    const visibleRadius = Math.max(420, cameraRange * 1.35)
    let changed = false
    for (const cell of this.cells) {
      const nextCellVisible = distanceMeters(center, cell.center) <= visibleRadius
      if (cell.group.visible !== nextCellVisible) {
        cell.group.visible = nextCellVisible
        changed = true
      }
      if (!nextCellVisible) continue
      for (const child of cell.group.children) {
        const kind = child.userData.vegetationKind as VegetationKind
        const stableDuringInteraction = kind === 'tree' || kind === 'hedge'
        const nextVisible = cameraRange <= KIND_MAX_CAMERA_RANGE[kind]
          && (!this.interactionActive || stableDuringInteraction)
        if (child.visible !== nextVisible) {
          child.visible = nextVisible
          changed = true
        }
      }
    }
    if (changed) this.engine.requestRender()
  }

  private clearResources(): void {
    if (this.visibilityTimer) clearInterval(this.visibilityTimer)
    this.visibilityTimer = null
    if (this.root) this.engine.remove(this.root)
    this.root = null
    this.cells = []
    if (this.sourceScene) disposeObject(this.sourceScene)
    this.sourceScene = null
  }
}

function averagePosition(points: Array<[number, number, number?]>): [number, number] {
  return [
    points.reduce((sum, point) => sum + point[0], 0) / points.length,
    points.reduce((sum, point) => sum + point[1], 0) / points.length,
  ]
}

function groupBy<T>(items: T[], keyFor: (item: T) => string): Map<string, T[]> {
  const result = new Map<string, T[]>()
  for (const item of items) {
    const key = keyFor(item)
    result.set(key, [...(result.get(key) ?? []), item])
  }
  return result
}

function distanceMeters(a: readonly number[], b: readonly number[]): number {
  const latitude = (a[1] + b[1]) / 2 * Math.PI / 180
  const dx = (a[0] - b[0]) * Math.cos(latitude) * 110_900
  const dy = (a[1] - b[1]) * 110_900
  return Math.hypot(dx, dy)
}

function disposeObject(object: Object3D): void {
  const geometries = new Set<BufferGeometry>()
  const materials = new Set<Material>()
  const textures = new Set<Texture>()
  object.traverse((candidate) => {
    if (!(candidate instanceof Mesh)) return
    geometries.add(candidate.geometry)
    const sourceMaterials = Array.isArray(candidate.material) ? candidate.material : [candidate.material]
    for (const material of sourceMaterials) {
      materials.add(material)
      for (const value of Object.values(material as unknown as Record<string, unknown>)) {
        if (value instanceof Texture) textures.add(value)
      }
    }
  })
  for (const geometry of geometries) geometry.dispose()
  for (const texture of textures) texture.dispose()
  for (const material of materials) material.dispose()
}
