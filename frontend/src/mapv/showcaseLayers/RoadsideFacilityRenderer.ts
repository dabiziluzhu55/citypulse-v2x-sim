import type { Engine } from '@baidumap/mapv-three'
import {
  BoxGeometry,
  BufferGeometry,
  Color,
  CylinderGeometry,
  Group,
  InstancedMesh,
  Material,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  Shape,
  ShapeGeometry,
  SphereGeometry,
} from 'three'
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
  private group: Group | null = null
  private manifest: SceneFacilityManifest | null = null
  private signalLenses: Record<SignalColor, InstancedMesh> | null = null

  constructor(engine: Engine, projector: RoadCoordinateProjector) {
    this.engine = engine
    this.projector = projector
  }

  render(manifest: SceneFacilityManifest): void {
    this.clear()
    const group = new Group()
    group.name = 'roadside-facilities'

    const polePoints = [
      ...manifest.lamps.map((point) => ({ point, height: 6 })),
      ...manifest.signals.map((point) => ({ point, height: 5.2 })),
      ...manifest.cameras.map((point) => ({ point, height: 5.2 })),
    ]
    const poles = new InstancedMesh(
      new CylinderGeometry(1, 1, 1, 8).rotateX(Math.PI / 2),
      new MeshStandardMaterial({ color: '#738495', roughness: 0.52, metalness: 0.48 }),
      polePoints.length,
    )
    poles.name = 'street-poles'
    polePoints.forEach(({ point, height }, index) => {
      poles.setMatrixAt(index, this.matrix(point, height / 2, [0.12, 0.12, height]))
    })
    group.add(poles)

    group.add(this.facilityMesh(
      'street-lamp-arms',
      new BoxGeometry(0.18, 1.7, 0.18).translate(0, -0.75, 5.96),
      '#7f91a0',
      manifest.lamps,
    ))
    group.add(this.facilityMesh(
      'street-lamp-housings',
      new BoxGeometry(0.56, 0.82, 0.24).translate(0, -1.55, 5.92),
      '#536472',
      manifest.lamps,
    ))
    group.add(this.facilityMesh(
      'street-lamp-lenses',
      new BoxGeometry(0.38, 0.08, 0.12).translate(0, -1.98, 5.86),
      '#ffe4a1',
      manifest.lamps,
      '#ffd77a',
    ))
    group.add(this.facilityMesh(
      'traffic-signal-arms',
      new BoxGeometry(0.18, 3.4, 0.18).translate(0, -1.58, 5.1),
      '#6f8190',
      manifest.signals,
    ))
    group.add(this.facilityMesh(
      'traffic-signal-backs',
      new BoxGeometry(0.92, 0.24, 1.7).translate(0, -3.22, 4.55),
      '#17222b',
      manifest.signals,
    ))
    group.add(this.facilityMesh(
      'roadside-camera-brackets',
      new BoxGeometry(0.16, 0.9, 0.16).translate(0, -0.4, 5.12),
      '#768896',
      manifest.cameras,
    ))
    group.add(this.facilityMesh(
      'roadside-cameras',
      new BoxGeometry(0.62, 0.82, 0.44).translate(0, -1.02, 5.1),
      '#8aa0b2',
      manifest.cameras,
    ))
    group.add(this.facilityMesh(
      'roadside-camera-lenses',
      new CylinderGeometry(0.14, 0.19, 0.18, 12).rotateX(Math.PI / 2).translate(0, -1.51, 5.1),
      '#182630',
      manifest.cameras,
    ))

    this.signalLenses = {
      red: this.signalLens('red', 5.05, manifest.signals),
      yellow: this.signalLens('yellow', 4.55, manifest.signals),
      green: this.signalLens('green', 4.05, manifest.signals),
    }
    group.add(this.signalLenses.red, this.signalLenses.yellow, this.signalLenses.green)

    const markings = new Map<string, SceneArrow[]>()
    for (const arrow of manifest.arrows) {
      const key = arrow.movements.join('-')
      markings.set(key, [...(markings.get(key) ?? []), arrow])
    }
    for (const [key, arrows] of markings) {
      group.add(this.arrowMesh(key, arrows))
    }

    this.group = this.engine.add(group)
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

  destroy(): void {
    this.clear()
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
      new MeshStandardMaterial({ color: '#f2f7fb', emissive: '#50606b', emissiveIntensity: 0.28, side: 2 }),
      arrows.length,
    )
    mesh.name = `road-arrow-${key}`
    arrows.forEach((arrow, index) => mesh.setMatrixAt(index, this.matrix(arrow, 0.27)))
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
    this.manifest = null
    this.signalLenses = null
  }
}
