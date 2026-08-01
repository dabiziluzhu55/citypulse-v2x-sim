import type { Engine } from '@baidumap/mapv-three'
import {
  BoxGeometry,
  Box3,
  BufferGeometry,
  Color,
  CylinderGeometry,
  Group,
  InstancedMesh,
  Material,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Object3D,
  Shape,
  ShapeGeometry,
  SphereGeometry,
  SRGBColorSpace,
  Texture,
  Vector3,
} from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { RoadCoordinateProjector } from '../roadGeometry'
import {
  resolveSignalColor,
  type SceneArrow,
  type SceneFacilityManifest,
  type SceneFacilityPoint,
  type SignalColor,
} from './sceneFacilities.ts'

export interface SignalRuntimeState {
  intersection_id: string
  current_phase: number
  stage: string
}

const ACTIVE_SIGNAL_COLORS: Record<SignalColor, Color> = {
  red: new Color('#ff3232'),
  yellow: new Color('#ffd84a'),
  green: new Color('#29f58a'),
}

const INACTIVE_SIGNAL_COLORS: Record<SignalColor, Color> = {
  red: new Color('#310b0b'),
  yellow: new Color('#302809'),
  green: new Color('#092b1b'),
}

export class RoadsideFacilityRenderer {
  private readonly engine: Engine
  private readonly projector: RoadCoordinateProjector
  private readonly transform = new Object3D()
  private readonly streetlightMatrix = new Matrix4()
  private group: Group | null = null
  private furnitureGroup: Group | null = null
  private legacySignalGroup: Group | null = null
  private legacyMarkingGroup: Group | null = null
  private manifest: SceneFacilityManifest | null = null
  private signalLenses: Record<SignalColor, InstancedMesh> | null = null
  private streetlightSource: Group | null = null
  private streetlightKey = ''

  constructor(engine: Engine, projector: RoadCoordinateProjector) {
    this.engine = engine
    this.projector = projector
  }

  async prepareStreetlight(modelUrl: string, heightMeters: number): Promise<void> {
    const key = `${modelUrl}:${heightMeters}`
    if (this.streetlightSource && this.streetlightKey === key) return
    const gltf = await new GLTFLoader().loadAsync(modelUrl)
    const bounds = new Box3().setFromObject(gltf.scene)
    const size = bounds.getSize(new Vector3())
    if (!Number.isFinite(size.y) || size.y <= 1e-6) {
      this.disposeSource(gltf.scene)
      throw new Error('Streetlight model has no measurable height')
    }
    const translated = new Group()
    translated.add(gltf.scene)
    translated.position.set(
      -(bounds.min.x + bounds.max.x) / 2,
      -bounds.min.y,
      -(bounds.min.z + bounds.max.z) / 2,
    )
    const normalized = new Group()
    normalized.add(translated)
    normalized.rotation.x = Math.PI / 2
    normalized.scale.setScalar(heightMeters / size.y)
    normalized.updateMatrixWorld(true)
    this.clearStreetlightSource()
    this.streetlightSource = normalized
    this.streetlightKey = key
  }

  render(manifest: SceneFacilityManifest): void {
    this.clear()
    const group = new Group()
    group.name = 'roadside-facilities'
    const furnitureGroup = new Group()
    furnitureGroup.name = 'roadside-furniture'
    const legacySignalGroup = new Group()
    legacySignalGroup.name = 'legacy-traffic-signals'
    const legacyMarkingGroup = new Group()
    legacyMarkingGroup.name = 'legacy-road-markings'
    group.add(furnitureGroup, legacySignalGroup, legacyMarkingGroup)

    const furniturePolePoints = manifest.cameras.map((point) => ({ point, height: 5.2 }))
    furnitureGroup.add(this.poleMesh('street-poles', furniturePolePoints))
    legacySignalGroup.add(this.poleMesh(
      'traffic-signal-poles',
      manifest.signals.map((point) => ({ point, height: 5.2 })),
    ))

    this.streetlightMeshes(manifest.lamps).forEach((mesh) => furnitureGroup.add(mesh))
    furnitureGroup.add(this.facilityMesh(
      'streetlight-emissive-lenses',
      new BoxGeometry(0.52, 0.86, 0.08).translate(0, -1.52, 7.18),
      '#fff2c2',
      manifest.lamps,
      '#ffd36a',
    ))
    legacySignalGroup.add(this.facilityMesh(
      'traffic-signal-arms',
      new BoxGeometry(0.18, 3.4, 0.18).translate(0, -1.58, 5.1),
      '#6f8190',
      manifest.signals,
    ))
    legacySignalGroup.add(this.facilityMesh(
      'traffic-signal-backs',
      new BoxGeometry(0.92, 0.24, 1.7).translate(0, -3.22, 4.55),
      '#17222b',
      manifest.signals,
    ))
    furnitureGroup.add(this.facilityMesh(
      'roadside-camera-brackets',
      new BoxGeometry(0.16, 0.9, 0.16).translate(0, -0.4, 5.12),
      '#768896',
      manifest.cameras,
    ))
    furnitureGroup.add(this.facilityMesh(
      'roadside-cameras',
      new BoxGeometry(0.62, 0.82, 0.44).translate(0, -1.02, 5.1),
      '#8aa0b2',
      manifest.cameras,
    ))
    furnitureGroup.add(this.facilityMesh(
      'roadside-camera-lenses',
      new CylinderGeometry(0.14, 0.19, 0.18, 12).rotateX(Math.PI / 2).translate(0, -1.51, 5.1),
      '#182630',
      manifest.cameras,
    ))
    furnitureGroup.add(this.facilityMesh(
      'roadside-control-cabinets',
      new BoxGeometry(0.82, 0.62, 1.28).translate(0, 1.12, 0.64),
      '#6f7e87',
      manifest.cameras,
    ))

    this.signalLenses = {
      red: this.signalLens('red', 5.05, manifest.signals),
      yellow: this.signalLens('yellow', 4.55, manifest.signals),
      green: this.signalLens('green', 4.05, manifest.signals),
    }
    legacySignalGroup.add(this.signalLenses.red, this.signalLenses.yellow, this.signalLenses.green)

    const markings = new Map<string, SceneArrow[]>()
    for (const arrow of manifest.arrows) {
      const key = arrow.movements.join('-')
      markings.set(key, [...(markings.get(key) ?? []), arrow])
    }
    for (const [key, arrows] of markings) {
      legacyMarkingGroup.add(this.arrowMesh(key, arrows))
    }

    this.group = this.engine.add(group)
    this.furnitureGroup = furnitureGroup
    this.legacySignalGroup = legacySignalGroup
    this.legacyMarkingGroup = legacyMarkingGroup
    this.manifest = manifest
    this.updateSignals(null)
  }

  updateSignals(intersections: SignalRuntimeState[] | null): void {
    if (!this.manifest || !this.signalLenses) return
    const runtime = intersections?.find(
      (item) => item.intersection_id === this.manifest?.intersectionId,
    )
    this.manifest.signals.forEach((signal, index) => {
      const active = resolveSignalColor(
        this.manifest!,
        signal,
        runtime?.current_phase ?? null,
        runtime?.stage ?? null,
      )
      for (const color of ['red', 'yellow', 'green'] as const) {
        this.signalLenses![color].setColorAt(
          index,
          color === active ? ACTIVE_SIGNAL_COLORS[color] : INACTIVE_SIGNAL_COLORS[color],
        )
      }
    })
    for (const mesh of Object.values(this.signalLenses)) {
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    }
    this.engine.requestRender()
  }

  setRealisticDetailActive(active: boolean): void {
    if (!this.group) return
    this.group.visible = true
    if (this.furnitureGroup) this.furnitureGroup.visible = true
    if (this.legacySignalGroup) this.legacySignalGroup.visible = !active
    if (this.legacyMarkingGroup) this.legacyMarkingGroup.visible = !active
    this.engine.requestRender()
  }

  destroy(): void {
    this.clear()
    this.clearStreetlightSource()
  }

  clearScene(): void {
    this.clear()
    this.engine.requestRender()
  }

  private matrix(
    point: SceneFacilityPoint,
    height = 0,
    scale: [number, number, number] = [1, 1, 1],
    headingOffset = 0,
  ) {
    const projected = this.projector(point.position)
    const geographicPosition = projected[2] == null
      ? [projected[0], projected[1]]
      : [projected[0], projected[1], projected[2]]
    const scene = this.engine.map.projectArrayCoordinate(geographicPosition)
    this.transform.position.set(scene[0], scene[1], (scene[2] ?? 0) + height)
    this.transform.rotation.set(0, 0, point.heading + headingOffset)
    this.transform.scale.set(...scale)
    this.transform.updateMatrix()
    return this.transform.matrix
  }

  private poleMesh(
    name: string,
    points: Array<{ point: SceneFacilityPoint, height: number }>,
  ): InstancedMesh {
    const mesh = new InstancedMesh(
      new CylinderGeometry(1, 1, 1, 8).rotateX(Math.PI / 2),
      new MeshStandardMaterial({ color: '#738495', roughness: 0.52, metalness: 0.48 }),
      points.length,
    )
    mesh.name = name
    points.forEach(({ point, height }, index) => {
      mesh.setMatrixAt(index, this.matrix(point, height / 2, [0.12, 0.12, height]))
    })
    return mesh
  }

  private facilityMesh(
    name: string,
    geometry: BufferGeometry,
    color: string,
    points: SceneFacilityPoint[],
    emissive?: string,
  ): InstancedMesh {
    const mesh = new InstancedMesh(
      geometry,
      new MeshStandardMaterial({
        color,
        emissive: emissive ?? '#000000',
        emissiveIntensity: emissive ? 1.4 : 0,
        roughness: emissive ? 0.3 : 0.58,
        metalness: emissive ? 0 : 0.3,
      }),
      points.length,
    )
    mesh.name = name
    points.forEach((point, index) => mesh.setMatrixAt(index, this.matrix(point)))
    return mesh
  }

  private streetlightMeshes(points: SceneFacilityPoint[]): InstancedMesh[] {
    if (!this.streetlightSource || points.length === 0) return []
    this.streetlightSource.updateMatrixWorld(true)
    const result: InstancedMesh[] = []
    this.streetlightSource.traverse((candidate) => {
      if (!(candidate instanceof Mesh)) return
      const sourceMaterials = Array.isArray(candidate.material) ? candidate.material : [candidate.material]
      const clonedMaterials = sourceMaterials.map((sourceMaterial) => {
        const material = sourceMaterial.clone()
        if (material instanceof MeshStandardMaterial) {
          material.roughness = Math.min(0.58, Math.max(0.32, material.roughness))
          material.metalness = Math.min(0.62, Math.max(0.18, material.metalness))
          material.color.multiplyScalar(1.28)
          material.emissive.set('#111820')
          material.emissiveIntensity = 0.2
          for (const texture of [material.map, material.normalMap, material.metalnessMap, material.roughnessMap]) {
            if (!texture) continue
            texture.anisotropy = Math.max(texture.anisotropy, 8)
            if (texture === material.map) texture.colorSpace = SRGBColorSpace
            texture.needsUpdate = true
          }
          material.needsUpdate = true
        }
        return material
      })
      const mesh = new InstancedMesh(
        candidate.geometry.clone(),
        Array.isArray(candidate.material) ? clonedMaterials : clonedMaterials[0],
        points.length,
      )
      mesh.name = `streetlight-model-${candidate.name || result.length}`
      points.forEach((point, index) => {
        this.streetlightMatrix.copy(this.matrix(point, 0.08)).multiply(candidate.matrixWorld)
        mesh.setMatrixAt(index, this.streetlightMatrix)
      })
      mesh.instanceMatrix.needsUpdate = true
      mesh.castShadow = true
      mesh.receiveShadow = true
      mesh.computeBoundingBox()
      mesh.computeBoundingSphere()
      mesh.frustumCulled = false
      result.push(mesh)
    })
    return result
  }

  private signalLens(
    color: SignalColor,
    height: number,
    points: SceneFacilityPoint[],
  ): InstancedMesh {
    const geometry = new SphereGeometry(0.22, 12, 8).scale(1, 0.45, 1).translate(0, -3.38, height)
    const mesh = new InstancedMesh(geometry, new MeshStandardMaterial({
      color: '#ffffff',
      emissive: '#202020',
      emissiveIntensity: 0.3,
      roughness: 0.25,
    }), points.length)
    mesh.name = `traffic-signal-${color}`
    points.forEach((point, index) => mesh.setMatrixAt(index, this.matrix(point)))
    return mesh
  }

  private arrowShape(key: string): Shape {
    const templates: Record<string, Array<[number, number]>> = {
      through: [
        [-0.2, -2.5], [0.2, -2.5], [0.2, 1.15], [0.66, 1.15],
        [0, 2.5], [-0.66, 1.15], [-0.2, 1.15],
      ],
      left: [
        [-0.2, -2.5], [0.2, -2.5], [0.2, 1.25], [-0.76, 1.25],
        [-0.76, 0.65], [-1.65, 1.45], [-0.76, 2.25], [-0.76, 1.65], [-0.2, 1.65],
      ],
      'left-through': [
        [-0.22, -2.5], [0.22, -2.5], [0.22, 1.1], [0.66, 1.1],
        [0, 2.5], [-0.66, 1.1], [-0.76, 1.1], [-0.76, 1.65],
        [-1.65, 0.85], [-0.76, 0.05], [-0.76, 0.6], [-0.22, 0.6],
      ],
    }
    const mirror = (points: Array<[number, number]>): Array<[number, number]> => (
      points.map(([x, y]) => [-x, y] as [number, number]).reverse()
    )
    templates.right = mirror(templates.left)
    templates['through-right'] = mirror(templates['left-through'])
    const points = templates[key] ?? templates.through
    const shape = new Shape()
    shape.moveTo(...points[0])
    points.slice(1).forEach(([x, y]) => shape.lineTo(x, y))
    shape.closePath()
    return shape
  }

  private arrowMesh(key: string, arrows: SceneArrow[]): InstancedMesh {
    const geometry = new ShapeGeometry(this.arrowShape(key))
    geometry.userData.contourCount = 1
    const mesh = new InstancedMesh(
      geometry,
      new MeshBasicMaterial({
        color: '#f2f7fb',
        side: 2,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -2,
        polygonOffsetUnits: -2,
      }),
      arrows.length,
    )
    mesh.name = `road-arrow-${key}`
    mesh.renderOrder = 24
    arrows.forEach((arrow, index) => mesh.setMatrixAt(index, this.matrix(arrow, 0.34)))
    return mesh
  }

  private clear(): void {
    if (!this.group) return
    this.engine.remove(this.group)
    this.group.traverse((object) => {
      if (!(object instanceof Mesh)) return
      object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach((material: Material) => material.dispose())
    })
    this.group = null
    this.furnitureGroup = null
    this.legacySignalGroup = null
    this.legacyMarkingGroup = null
    this.manifest = null
    this.signalLenses = null
  }

  private clearStreetlightSource(): void {
    if (!this.streetlightSource) return
    this.disposeSource(this.streetlightSource)
    this.streetlightSource = null
    this.streetlightKey = ''
  }

  private disposeSource(object: Object3D): void {
    const geometries = new Set<BufferGeometry>()
    const materials = new Set<Material>()
    const textures = new Set<Texture>()
    object.traverse((candidate) => {
      if (!(candidate instanceof Mesh)) return
      geometries.add(candidate.geometry)
      const sourceMaterials = Array.isArray(candidate.material) ? candidate.material : [candidate.material]
      sourceMaterials.forEach((material) => {
        materials.add(material)
        Object.values(material).forEach((value) => {
          if (value instanceof Texture) textures.add(value)
        })
      })
    })
    geometries.forEach((geometry) => geometry.dispose())
    materials.forEach((material) => material.dispose())
    textures.forEach((texture) => texture.dispose())
  }
}
