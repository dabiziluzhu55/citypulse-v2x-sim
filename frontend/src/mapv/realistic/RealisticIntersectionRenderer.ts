import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type {
  Point2,
  RealisticConnection,
  RealisticIntersectionManifest,
  RealisticLane,
} from './intersectionManifest'
import { signalColorForState } from './intersectionManifest'

export type DemoCameraPreset = 'overview' | 'signals' | 'markings'

interface SignalHeadMaterials {
  linkIndex: number
  red: THREE.MeshStandardMaterial
  amber: THREE.MeshStandardMaterial
  green: THREE.MeshStandardMaterial
  redGlow: THREE.SpriteMaterial
  amberGlow: THREE.SpriteMaterial
  greenGlow: THREE.SpriteMaterial
}

interface AnimatedVehicle {
  object: THREE.Group
  curve: THREE.CatmullRomCurve3
  speed: number
  offset: number
}

const COLORS = {
  asphalt: 0x747574,
  asphaltJunction: 0x6c6e6d,
  concrete: 0x777b7a,
  marking: 0xe7e5d8,
  yellow: 0xe1b63c,
  pole: 0x50585a,
}

function createCanvasTexture(
  width: number,
  height: number,
  draw: (context: CanvasRenderingContext2D) => void,
): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Canvas 2D context is unavailable')
  draw(context)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 8
  return texture
}

function makeAsphaltTexture(): THREE.CanvasTexture {
  return createCanvasTexture(256, 256, (context) => {
    context.fillStyle = '#3c3e3e'
    context.fillRect(0, 0, 256, 256)
    let seed = 317
    for (let index = 0; index < 5600; index += 1) {
      seed = (seed * 16807) % 2147483647
      const x = seed % 256
      seed = (seed * 16807) % 2147483647
      const y = seed % 256
      seed = (seed * 16807) % 2147483647
      const shade = 38 + (seed % 34)
      context.fillStyle = `rgba(${shade},${shade},${shade},0.34)`
      context.fillRect(x, y, 1, 1)
    }
    context.strokeStyle = 'rgba(16, 17, 17, 0.24)'
    context.lineWidth = 1
    for (let y = 16; y < 256; y += 32) {
      context.beginPath()
      context.moveTo(0, y)
      context.lineTo(256, y + 8)
      context.stroke()
    }
  })
}

function makeWindowTexture(): THREE.CanvasTexture {
  const texture = createCanvasTexture(128, 128, (context) => {
    context.fillStyle = '#263039'
    context.fillRect(0, 0, 128, 128)
    for (let y = 10; y < 120; y += 22) {
      for (let x = 8; x < 120; x += 22) {
        const lit = (x * 7 + y * 11) % 5 !== 0
        context.fillStyle = lit ? '#d8b778' : '#111a20'
        context.fillRect(x, y, 12, 10)
      }
    }
  })
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(2, 2)
  return texture
}

function makeGlowTexture(): THREE.CanvasTexture {
  return createCanvasTexture(128, 128, (context) => {
    const gradient = context.createRadialGradient(64, 64, 2, 64, 64, 62)
    gradient.addColorStop(0, 'rgba(255,255,255,0.98)')
    gradient.addColorStop(0.22, 'rgba(255,255,255,0.72)')
    gradient.addColorStop(0.55, 'rgba(255,255,255,0.2)')
    gradient.addColorStop(1, 'rgba(255,255,255,0)')
    context.fillStyle = gradient
    context.fillRect(0, 0, 128, 128)
  })
}

function makeDirectionTexture(direction: RealisticConnection['direction']): THREE.CanvasTexture {
  return createCanvasTexture(128, 96, (context) => {
    context.clearRect(0, 0, 128, 96)
    context.strokeStyle = '#f3f6ee'
    context.fillStyle = '#f3f6ee'
    context.lineWidth = 12
    context.lineCap = 'round'
    context.lineJoin = 'round'
    context.beginPath()
    if (direction === 's') {
      context.moveTo(64, 78)
      context.lineTo(64, 25)
      context.lineTo(42, 47)
      context.moveTo(64, 25)
      context.lineTo(86, 47)
    } else if (direction === 'l') {
      context.moveTo(78, 78)
      context.lineTo(78, 52)
      context.quadraticCurveTo(78, 25, 48, 25)
      context.lineTo(27, 25)
      context.moveTo(27, 25)
      context.lineTo(48, 8)
      context.moveTo(27, 25)
      context.lineTo(48, 43)
    } else {
      context.moveTo(50, 78)
      context.lineTo(50, 52)
      context.quadraticCurveTo(50, 25, 80, 25)
      context.lineTo(101, 25)
      context.moveTo(101, 25)
      context.lineTo(80, 8)
      context.moveTo(101, 25)
      context.lineTo(80, 43)
    }
    context.stroke()
  })
}

function makeRoadArrowTexture(directions: RealisticConnection['direction'][]): THREE.CanvasTexture {
  return createCanvasTexture(256, 512, (context) => {
    context.clearRect(0, 0, 256, 512)
    context.strokeStyle = '#f0eee3'
    context.fillStyle = '#f0eee3'
    context.lineWidth = 28
    context.lineCap = 'round'
    context.lineJoin = 'round'
    const drawArrow = (direction: RealisticConnection['direction'], x: number) => {
      context.beginPath()
      if (direction === 's') {
        context.moveTo(x, 420)
        context.lineTo(x, 112)
        context.moveTo(x, 112)
        context.lineTo(x - 40, 170)
        context.moveTo(x, 112)
        context.lineTo(x + 40, 170)
      } else {
        const sign = direction === 'l' ? -1 : 1
        context.moveTo(x, 420)
        context.lineTo(x, 250)
        context.quadraticCurveTo(x, 150, x + sign * 62, 150)
        context.lineTo(x + sign * 88, 150)
        context.moveTo(x + sign * 88, 150)
        context.lineTo(x + sign * 50, 110)
        context.moveTo(x + sign * 88, 150)
        context.lineTo(x + sign * 50, 190)
      }
      context.stroke()
    }
    if (directions.length === 1) drawArrow(directions[0], 128)
    else directions.forEach((direction, index) => drawArrow(direction, index === 0 ? 90 : 166))
  })
}

function stripGeometry(points: Point2[], width: number, z: number): THREE.BufferGeometry {
  const positions: number[] = []
  const uvs: number[] = []
  const indices: number[] = []
  let distance = 0
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index]
    const previous = points[Math.max(0, index - 1)]
    const next = points[Math.min(points.length - 1, index + 1)]
    if (index > 0) distance += Math.hypot(current[0] - previous[0], current[1] - previous[1])
    const dx = next[0] - previous[0]
    const dy = next[1] - previous[1]
    const length = Math.hypot(dx, dy) || 1
    const nx = -dy / length
    const ny = dx / length
    positions.push(
      current[0] + nx * width / 2, current[1] + ny * width / 2, z,
      current[0] - nx * width / 2, current[1] - ny * width / 2, z,
    )
    uvs.push(0, distance / 8, 1, distance / 8)
    if (index < points.length - 1) {
      const base = index * 2
      indices.push(base, base + 1, base + 2, base + 1, base + 3, base + 2)
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2))
  geometry.setIndex(indices)
  geometry.computeVertexNormals()
  return geometry
}

function pointAndTangent(lane: RealisticLane, distanceFromJunction: number, incoming: boolean) {
  const points = lane.points
  const startIndex = incoming ? points.length - 1 : 0
  const step = incoming ? -1 : 1
  let remaining = distanceFromJunction
  let current = points[startIndex]
  let index = startIndex
  while (index + step >= 0 && index + step < points.length) {
    const next = points[index + step]
    const length = Math.hypot(next[0] - current[0], next[1] - current[1])
    if (length >= remaining) {
      const ratio = remaining / length
      const point: Point2 = [
        current[0] + (next[0] - current[0]) * ratio,
        current[1] + (next[1] - current[1]) * ratio,
      ]
      const towardJunction = incoming
        ? [current[0] - next[0], current[1] - next[1]]
        : [current[0] - next[0], current[1] - next[1]]
      const tangentLength = Math.hypot(towardJunction[0], towardJunction[1]) || 1
      return { point, tangent: [towardJunction[0] / tangentLength, towardJunction[1] / tangentLength] as Point2 }
    }
    remaining -= length
    current = next
    index += step
  }
  const fallback = points[Math.max(0, Math.min(points.length - 1, index))]
  return { point: fallback, tangent: [1, 0] as Point2 }
}

function boxBetween(
  start: Point2,
  end: Point2,
  z: number,
  thickness: number,
  material: THREE.Material,
): THREE.Mesh {
  const length = Math.hypot(end[0] - start[0], end[1] - start[1])
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(length, thickness, thickness), material)
  mesh.position.set((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, z)
  mesh.rotation.z = Math.atan2(end[1] - start[1], end[0] - start[0])
  mesh.castShadow = true
  return mesh
}

function markingGeometryBetween(
  start: Point2,
  end: Point2,
  width: number,
): THREE.BufferGeometry {
  const length = Math.hypot(end[0] - start[0], end[1] - start[1])
  const geometry = new THREE.BoxGeometry(length, width, 0.012)
  geometry.rotateZ(Math.atan2(end[1] - start[1], end[0] - start[0]))
  geometry.translate((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, 0.008)
  return geometry
}

function zCylinder(radius: number, height: number, material: THREE.Material): THREE.Mesh {
  const geometry = new THREE.CylinderGeometry(radius, radius * 1.04, height, 24)
  geometry.rotateX(Math.PI / 2)
  const mesh = new THREE.Mesh(geometry, material)
  mesh.castShadow = true
  return mesh
}

function applyLensState(
  material: THREE.MeshStandardMaterial,
  glow: THREE.SpriteMaterial,
  active: boolean,
  color: number,
) {
  material.color.setHex(active ? color : 0x181919)
  material.emissive.setHex(active ? color : 0x050505)
  material.emissiveIntensity = active ? 7 : 0.18
  material.roughness = active ? 0.18 : 0.6
  glow.color.setHex(color)
  glow.opacity = active ? 0.82 : 0
}

export class RealisticIntersectionRenderer {
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.PerspectiveCamera(46, 1, 0.1, 1200)
  private readonly renderer: THREE.WebGLRenderer
  private readonly controls: OrbitControls
  private readonly signalHeads: SignalHeadMaterials[] = []
  private readonly vehicles: AnimatedVehicle[] = []
  private readonly upAxis = new THREE.Vector3(0, 0, 1)
  private readonly glowTexture = makeGlowTexture()
  private readonly resizeObserver: ResizeObserver
  private frameHandle = 0
  private disposed = false
  private autoOrbit = false
  private lastFrameAt = performance.now()
  private fps = 60
  private elapsedSeconds = 0

  constructor(
    private readonly container: HTMLElement,
    private readonly manifest: RealisticIntersectionManifest,
    private readonly onPerformance?: (fps: number) => void,
  ) {
    this.scene.background = new THREE.Color(0x101820)
    this.scene.fog = new THREE.FogExp2(0x17232b, 0.0042)
    this.camera.up.set(0, 0, 1)
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      logarithmicDepthBuffer: false,
      powerPreference: 'high-performance',
    })
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.28
    this.renderer.shadowMap.enabled = false
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.35))
    this.container.appendChild(this.renderer.domElement)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.minDistance = 18
    this.controls.maxDistance = 260
    this.controls.maxPolarAngle = Math.PI * 0.48
    this.controls.target.set(0, 0, 1)

    this.buildLighting()
    this.buildEnvironment()
    this.buildRoadsAndMarkings()
    this.buildSignals()
    this.buildVehicles()
    this.setCameraPreset('overview', false)
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(this.container)
    this.resize()
    this.animate()
  }

  setSignalState(state: string): void {
    for (const head of this.signalHeads) {
      const active = signalColorForState(state, head.linkIndex)
      applyLensState(head.red, head.redGlow, active === 'red', 0xff2d24)
      applyLensState(head.amber, head.amberGlow, active === 'amber', 0xffb21c)
      applyLensState(head.green, head.greenGlow, active === 'green', 0x39ff77)
    }
  }

  setAutoOrbit(enabled: boolean): void {
    this.autoOrbit = enabled
  }

  setCameraPreset(preset: DemoCameraPreset, animate = true): void {
    const settings: Record<DemoCameraPreset, { position: THREE.Vector3; target: THREE.Vector3 }> = {
      overview: { position: new THREE.Vector3(78, -92, 92), target: new THREE.Vector3(0, 0, 2) },
      signals: { position: new THREE.Vector3(28, -37, 11), target: new THREE.Vector3(1, 0, 4.2) },
      markings: { position: new THREE.Vector3(1, -22, 112), target: new THREE.Vector3(0, 0, 0) },
    }
    const next = settings[preset]
    if (!animate) {
      this.camera.position.copy(next.position)
      this.controls.target.copy(next.target)
      this.controls.update()
      return
    }
    const startPosition = this.camera.position.clone()
    const startTarget = this.controls.target.clone()
    const startedAt = performance.now()
    const update = () => {
      if (this.disposed) return
      const progress = Math.min(1, (performance.now() - startedAt) / 620)
      const eased = 1 - Math.pow(1 - progress, 3)
      this.camera.position.lerpVectors(startPosition, next.position, eased)
      this.controls.target.lerpVectors(startTarget, next.target, eased)
      if (progress < 1) requestAnimationFrame(update)
    }
    requestAnimationFrame(update)
  }

  dispose(): void {
    this.disposed = true
    cancelAnimationFrame(this.frameHandle)
    this.resizeObserver.disconnect()
    this.controls.dispose()
    this.scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh) && !(object instanceof THREE.Sprite)) return
      if (object instanceof THREE.Mesh) object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach((material) => {
        const map = (material as THREE.MeshStandardMaterial).map
        map?.dispose()
        material.dispose()
      })
    })
    this.renderer.dispose()
    this.glowTexture.dispose()
    this.renderer.domElement.remove()
  }

  private resize(): void {
    const width = Math.max(1, this.container.clientWidth)
    const height = Math.max(1, this.container.clientHeight)
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height, false)
  }

  private buildLighting(): void {
    const hemisphere = new THREE.HemisphereLight(0x89a9c6, 0x26302b, 1.45)
    this.scene.add(hemisphere)
    const sun = new THREE.DirectionalLight(0xffd6ad, 3.2)
    sun.position.set(-65, -45, 110)
    this.scene.add(sun)
    const dusk = new THREE.DirectionalLight(0x4e8bb6, 0.7)
    dusk.position.set(80, 60, 45)
    this.scene.add(dusk)
  }

  private buildEnvironment(): void {
    const groundMaterial = new THREE.MeshStandardMaterial({ color: 0x26342e, roughness: 1 })
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(420, 420), groundMaterial)
    ground.position.z = -0.24
    ground.receiveShadow = true
    this.scene.add(ground)

    const windowTexture = makeWindowTexture()
    const buildingMaterial = new THREE.MeshStandardMaterial({
      color: 0x6e7578,
      map: windowTexture,
      roughness: 0.82,
      emissive: 0x201c15,
      emissiveIntensity: 0.34,
      emissiveMap: windowTexture,
    })
    const blocks = [
      [-105, -82, 24, 34, 22], [-76, -105, 27, 20, 15], [-35, -116, 34, 21, 29],
      [94, -86, 26, 31, 18], [115, -42, 22, 35, 25], [105, 82, 32, 23, 21],
      [68, 108, 28, 22, 32], [-72, 110, 34, 26, 17], [-110, 70, 23, 32, 24],
    ] as const
    for (const [x, y, width, depth, height] of blocks) {
      const building = new THREE.Mesh(new THREE.BoxGeometry(width, depth, height), buildingMaterial)
      building.position.set(x, y, height / 2 - 0.1)
      building.castShadow = true
      building.receiveShadow = true
      this.scene.add(building)
    }

    const trunkMaterial = new THREE.MeshStandardMaterial({ color: 0x4b392b, roughness: 1 })
    const crownMaterial = new THREE.MeshStandardMaterial({ color: 0x315843, roughness: 0.9 })
    const treePositions = [[-65, -75], [-85, -54], [80, -62], [90, 60], [55, 88], [-72, 82]]
    for (const [x, y] of treePositions) {
      const trunk = zCylinder(0.32, 4.2, trunkMaterial)
      trunk.position.set(x, y, 2.1)
      const crown = new THREE.Mesh(new THREE.IcosahedronGeometry(2.6, 2), crownMaterial)
      crown.position.set(x, y, 5.2)
      crown.castShadow = true
      this.scene.add(trunk, crown)
    }
  }

  private buildRoadsAndMarkings(): void {
    const asphaltTexture = makeAsphaltTexture()
    asphaltTexture.wrapS = THREE.RepeatWrapping
    asphaltTexture.wrapT = THREE.RepeatWrapping
    asphaltTexture.repeat.set(0.7, 0.7)
    const asphalt = new THREE.MeshStandardMaterial({
      color: COLORS.asphalt,
      map: asphaltTexture,
      roughness: 0.94,
      metalness: 0.02,
    })
    const shoulder = new THREE.MeshStandardMaterial({ color: COLORS.concrete, roughness: 0.88 })
    const marking = new THREE.MeshStandardMaterial({
      color: COLORS.marking,
      roughness: 0.62,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    })
    const yellow = new THREE.MeshStandardMaterial({
      color: COLORS.yellow,
      roughness: 0.65,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    })
    const whiteMarkingGeometries: THREE.BufferGeometry[] = []
    const yellowMarkingGeometries: THREE.BufferGeometry[] = []

    for (const edge of this.manifest.edges) {
      for (const lane of edge.lanes) {
        const curb = new THREE.Mesh(stripGeometry(lane.points, lane.width + 0.42, -0.04), shoulder)
        curb.receiveShadow = true
        const road = new THREE.Mesh(stripGeometry(lane.points, lane.width + 0.06, 0), asphalt)
        road.receiveShadow = true
        this.scene.add(curb, road)
      }
      this.addLaneDivider(edge.lanes, whiteMarkingGeometries)
    }

    const junctionShape = new THREE.Shape(this.manifest.junctionShape.map(([x, y]) => new THREE.Vector2(x, y)))
    const junction = new THREE.Mesh(
      new THREE.ShapeGeometry(junctionShape),
      asphalt.clone(),
    )
    ;(junction.material as THREE.MeshStandardMaterial).color.setHex(COLORS.asphaltJunction)
    junction.position.z = 0.006
    junction.receiveShadow = true
    this.scene.add(junction)

    for (const edge of this.manifest.edges.filter((candidate) => candidate.incoming)) {
      this.addStopLine(edge.lanes, whiteMarkingGeometries)
      this.addCrosswalk(edge.lanes, whiteMarkingGeometries)
      for (const lane of edge.lanes) this.addLaneArrow(lane, edge.id, marking)
    }
    this.addCenterGuides(yellowMarkingGeometries)

    const whiteGeometry = mergeGeometries(whiteMarkingGeometries, false)
    if (whiteGeometry) {
      const whiteMarkings = new THREE.Mesh(whiteGeometry, marking)
      whiteMarkings.receiveShadow = true
      whiteMarkings.renderOrder = 2
      this.scene.add(whiteMarkings)
    }
    const yellowGeometry = mergeGeometries(yellowMarkingGeometries, false)
    if (yellowGeometry) {
      const yellowMarkings = new THREE.Mesh(yellowGeometry, yellow)
      yellowMarkings.receiveShadow = true
      yellowMarkings.renderOrder = 2
      this.scene.add(yellowMarkings)
    }
    whiteMarkingGeometries.forEach((geometry) => geometry.dispose())
    yellowMarkingGeometries.forEach((geometry) => geometry.dispose())
  }

  private addLaneDivider(lanes: RealisticLane[], geometries: THREE.BufferGeometry[]): void {
    const first = lanes[0].points
    const second = lanes[1].points
    const count = Math.min(first.length, second.length)
    if (count < 2) return
    const points: Point2[] = []
    for (let index = 0; index < count; index += 1) {
      points.push([(first[index][0] + second[index][0]) / 2, (first[index][1] + second[index][1]) / 2])
    }
    let carried = 0
    for (let index = 0; index < points.length - 1; index += 1) {
      const start = points[index]
      const end = points[index + 1]
      const length = Math.hypot(end[0] - start[0], end[1] - start[1])
      const pieces = Math.max(1, Math.ceil(length / 3))
      for (let piece = 0; piece < pieces; piece += 1) {
        const a = piece / pieces
        const b = (piece + 1) / pieces
        if (Math.floor((carried + a * length) / 3) % 2 === 0) {
          const p1: Point2 = [start[0] + (end[0] - start[0]) * a, start[1] + (end[1] - start[1]) * a]
          const p2: Point2 = [start[0] + (end[0] - start[0]) * b, start[1] + (end[1] - start[1]) * b]
          geometries.push(markingGeometryBetween(p1, p2, 0.11))
        }
      }
      carried += length
    }
  }

  private addStopLine(lanes: RealisticLane[], geometries: THREE.BufferGeometry[]): void {
    const samples = lanes.map((lane) => pointAndTangent(lane, 5.5, true))
    const center: Point2 = [
      (samples[0].point[0] + samples[1].point[0]) / 2,
      (samples[0].point[1] + samples[1].point[1]) / 2,
    ]
    const tangent = samples[0].tangent
    const normal: Point2 = [-tangent[1], tangent[0]]
    const halfWidth = 3.65
    geometries.push(markingGeometryBetween(
      [center[0] - normal[0] * halfWidth, center[1] - normal[1] * halfWidth],
      [center[0] + normal[0] * halfWidth, center[1] + normal[1] * halfWidth],
      0.42,
    ))
  }

  private addCrosswalk(lanes: RealisticLane[], geometries: THREE.BufferGeometry[]): void {
    const samples = lanes.map((lane) => pointAndTangent(lane, 10.5, true))
    const incomingCenter: Point2 = [
      (samples[0].point[0] + samples[1].point[0]) / 2,
      (samples[0].point[1] + samples[1].point[1]) / 2,
    ]
    const outgoingCenters = this.manifest.edges
      .filter((edge) => !edge.incoming)
      .map((edge) => {
        const edgeSamples = edge.lanes.map((lane) => pointAndTangent(lane, 10.5, false))
        return [
          (edgeSamples[0].point[0] + edgeSamples[1].point[0]) / 2,
          (edgeSamples[0].point[1] + edgeSamples[1].point[1]) / 2,
        ] as Point2
      })
      .sort((a, b) => (
        Math.hypot(a[0] - incomingCenter[0], a[1] - incomingCenter[1])
        - Math.hypot(b[0] - incomingCenter[0], b[1] - incomingCenter[1])
      ))
    const outgoingCenter = outgoingCenters[0] ?? incomingCenter
    const center: Point2 = [
      (incomingCenter[0] + outgoingCenter[0]) / 2,
      (incomingCenter[1] + outgoingCenter[1]) / 2,
    ]
    const tangent = samples[0].tangent
    const normal: Point2 = [-tangent[1], tangent[0]]
    const halfWidth = Math.hypot(
      outgoingCenter[0] - incomingCenter[0],
      outgoingCenter[1] - incomingCenter[1],
    ) / 2 + 3.55
    for (let stripe = -4; stripe <= 4; stripe += 1) {
      const offset = stripe * 0.72
      const stripeCenter: Point2 = [center[0] + tangent[0] * offset, center[1] + tangent[1] * offset]
      geometries.push(markingGeometryBetween(
        [stripeCenter[0] - normal[0] * halfWidth, stripeCenter[1] - normal[1] * halfWidth],
        [stripeCenter[0] + normal[0] * halfWidth, stripeCenter[1] + normal[1] * halfWidth],
        0.38,
      ))
    }
  }

  private addLaneArrow(lane: RealisticLane, edgeId: string, material: THREE.MeshStandardMaterial): void {
    const directions = this.manifest.connections
      .filter((connection) => connection.fromEdge === edgeId && connection.fromLane === lane.index)
      .map((connection) => connection.direction)
    if (directions.length === 0) return
    const sample = pointAndTangent(lane, 24, true)
    const arrowMaterial = material.clone()
    arrowMaterial.map = makeRoadArrowTexture(directions)
    arrowMaterial.transparent = true
    arrowMaterial.alphaTest = 0.12
    arrowMaterial.depthWrite = false
    const arrow = new THREE.Mesh(new THREE.PlaneGeometry(2.55, 6.4), arrowMaterial)
    arrow.position.set(sample.point[0], sample.point[1], 0.012)
    arrow.rotation.z = Math.atan2(sample.tangent[1], sample.tangent[0]) - Math.PI / 2
    arrow.renderOrder = 3
    this.scene.add(arrow)
  }

  private addCenterGuides(geometries: THREE.BufferGeometry[]): void {
    const incoming = this.manifest.edges.filter((edge) => edge.incoming)
    for (const edge of incoming) {
      const samples = edge.lanes.map((lane) => pointAndTangent(lane, 42, true))
      const center: Point2 = [
        (samples[0].point[0] + samples[1].point[0]) / 2,
        (samples[0].point[1] + samples[1].point[1]) / 2,
      ]
      const tangent = samples[0].tangent
      const normal: Point2 = [-tangent[1], tangent[0]]
      const start: Point2 = [center[0] + normal[0] * 3.6, center[1] + normal[1] * 3.6]
      const end: Point2 = [start[0] - tangent[0] * 48, start[1] - tangent[1] * 48]
      geometries.push(markingGeometryBetween(start, end, 0.1))
    }
  }

  private buildSignals(): void {
    const poleMaterial = new THREE.MeshStandardMaterial({
      color: COLORS.pole,
      roughness: 0.34,
      metalness: 0.76,
    })
    const housingMaterial = new THREE.MeshStandardMaterial({ color: 0x111416, roughness: 0.42, metalness: 0.26 })
    const visorMaterial = new THREE.MeshStandardMaterial({ color: 0x090a0b, roughness: 0.5 })

    for (const edge of this.manifest.edges.filter((candidate) => candidate.incoming)) {
      const laneSamples = edge.lanes.map((lane) => pointAndTangent(lane, 8.3, true))
      const tangent = laneSamples[0].tangent
      const normal: Point2 = [-tangent[1], tangent[0]]
      const center: Point2 = [
        (laneSamples[0].point[0] + laneSamples[1].point[0]) / 2,
        (laneSamples[0].point[1] + laneSamples[1].point[1]) / 2,
      ]
      const poleBase: Point2 = [center[0] + normal[0] * 6.7, center[1] + normal[1] * 6.7]
      const pole = zCylinder(0.16, 6.1, poleMaterial)
      pole.position.set(poleBase[0], poleBase[1], 3.05)
      this.scene.add(pole)

      const armEnd: Point2 = [center[0] - normal[0] * 2.25, center[1] - normal[1] * 2.25]
      this.scene.add(boxBetween(poleBase, armEnd, 5.85, 0.2, poleMaterial))
      const base = zCylinder(0.34, 0.32, poleMaterial)
      base.position.set(poleBase[0], poleBase[1], 0.16)
      this.scene.add(base)

      for (let laneIndex = 0; laneIndex < edge.lanes.length; laneIndex += 1) {
        const lane = edge.lanes[laneIndex]
        const connections = this.manifest.connections.filter(
          (connection) => connection.fromEdge === edge.id && connection.fromLane === lane.index,
        )
        connections.forEach((connection, connectionIndex) => {
          const shift = connections.length > 1 ? (connectionIndex - (connections.length - 1) / 2) * 0.62 : 0
          const point: Point2 = [
            laneSamples[laneIndex].point[0] + normal[0] * shift,
            laneSamples[laneIndex].point[1] + normal[1] * shift,
          ]
          const head = this.createSignalHead(connection, housingMaterial, visorMaterial)
          head.position.set(point[0], point[1], 5.54)
          head.rotation.z = Math.atan2(-tangent[0], tangent[1])
          this.scene.add(head)
        })
      }
    }
  }

  private createSignalHead(
    connection: RealisticConnection,
    housingMaterial: THREE.Material,
    visorMaterial: THREE.Material,
  ): THREE.Group {
    const group = new THREE.Group()
    const housing = new THREE.Mesh(new THREE.BoxGeometry(0.64, 0.32, 1.75), housingMaterial)
    housing.castShadow = true
    group.add(housing)

    const lensMaterials = [0xff2d24, 0xffb21c, 0x39ff77].map((color) => new THREE.MeshStandardMaterial({
      color: 0x181919,
      emissive: color,
      emissiveIntensity: 0.15,
      roughness: 0.58,
      metalness: 0.02,
    }))
    lensMaterials.forEach((material) => { material.toneMapped = false })
    const glowMaterials = [0xff2d24, 0xffb21c, 0x39ff77].map((color) => new THREE.SpriteMaterial({
      map: this.glowTexture,
      color,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      toneMapped: false,
    }))
    const lensPositions = [0.57, 0, -0.57]
    for (let index = 0; index < lensPositions.length; index += 1) {
      const lens = new THREE.Mesh(new THREE.SphereGeometry(0.2, 28, 18), lensMaterials[index])
      lens.scale.y = 0.42
      lens.position.set(0, -0.2, lensPositions[index])
      group.add(lens)
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.225, 0.027, 10, 30), visorMaterial)
      ring.rotation.x = Math.PI / 2
      ring.position.set(0, -0.219, lensPositions[index])
      group.add(ring)
      const visor = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.22, 0.055), visorMaterial)
      visor.rotation.x = Math.PI / 2
      visor.position.set(0, -0.285, lensPositions[index] + 0.2)
      group.add(visor)
      const glow = new THREE.Sprite(glowMaterials[index])
      glow.position.set(0, -0.31, lensPositions[index])
      glow.scale.set(0.72, 0.72, 0.72)
      group.add(glow)
    }

    const plateMaterial = new THREE.MeshBasicMaterial({
      map: makeDirectionTexture(connection.direction),
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
    })
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.3), plateMaterial)
    plate.rotation.x = Math.PI / 2
    plate.position.set(0, -0.151, -0.96)
    group.add(plate)
    const plateBack = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.12, 0.36), housingMaterial)
    plateBack.position.z = -0.96
    group.add(plateBack)

    this.signalHeads.push({
      linkIndex: connection.linkIndex,
      red: lensMaterials[0],
      amber: lensMaterials[1],
      green: lensMaterials[2],
      redGlow: glowMaterials[0],
      amberGlow: glowMaterials[1],
      greenGlow: glowMaterials[2],
    })
    return group
  }

  private buildVehicles(): void {
    const colors = [0xe8e8e5, 0x1b6ea8, 0x9c2f32, 0xd2a438, 0x58636b, 0xeeeeee]
    const lanes = this.manifest.edges.flatMap((edge) => edge.lanes.map((lane) => ({ lane, incoming: edge.incoming })))
    lanes.forEach(({ lane, incoming }, index) => {
      if (index % 2 !== 0) return
      const car = this.createVehicle(colors[index % colors.length])
      const curvePoints = lane.points.map(([x, y]) => new THREE.Vector3(x, y, 0.36))
      if (!incoming) curvePoints.reverse()
      const curve = new THREE.CatmullRomCurve3(curvePoints, false, 'centripetal')
      this.vehicles.push({ object: car, curve, speed: 0.025 + (index % 3) * 0.004, offset: index * 0.137 })
      this.scene.add(car)
    })
  }

  private createVehicle(color: number): THREE.Group {
    const group = new THREE.Group()
    const bodyMaterial = new THREE.MeshStandardMaterial({ color, roughness: 0.32, metalness: 0.45 })
    const glassMaterial = new THREE.MeshStandardMaterial({ color: 0x193240, roughness: 0.18, metalness: 0.35 })
    const tireMaterial = new THREE.MeshStandardMaterial({ color: 0x101112, roughness: 0.84 })
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.82, 4.25, 0.62), bodyMaterial)
    body.position.z = 0.48
    const cabin = new THREE.Mesh(new THREE.BoxGeometry(1.58, 2.02, 0.62), glassMaterial)
    cabin.position.set(0, -0.18, 0.96)
    body.castShadow = true
    cabin.castShadow = true
    group.add(body, cabin)
    for (const x of [-0.92, 0.92]) {
      for (const y of [-1.35, 1.35]) {
        const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.18, 18), tireMaterial)
        wheel.rotation.z = Math.PI / 2
        wheel.position.set(x, y, 0.28)
        group.add(wheel)
      }
    }
    return group
  }

  private animate = (): void => {
    if (this.disposed) return
    this.frameHandle = requestAnimationFrame(this.animate)
    const now = performance.now()
    const delta = Math.min(0.05, (now - this.lastFrameAt) / 1000)
    this.lastFrameAt = now
    this.elapsedSeconds += delta
    const instantFps = 1 / Math.max(delta, 0.001)
    this.fps += (instantFps - this.fps) * 0.04
    if (Math.floor(now / 500) !== Math.floor((now - delta * 1000) / 500)) this.onPerformance?.(Math.round(this.fps))

    for (const vehicle of this.vehicles) {
      const progress = (vehicle.offset + this.elapsedSeconds * vehicle.speed) % 1
      const point = vehicle.curve.getPointAt(progress)
      const tangent = vehicle.curve.getTangentAt(progress)
      vehicle.object.position.copy(point)
      vehicle.object.rotation.z = Math.atan2(tangent.y, tangent.x) - Math.PI / 2
    }
    if (this.autoOrbit) {
      this.camera.position
        .sub(this.controls.target)
        .applyAxisAngle(this.upAxis, delta * 0.085)
        .add(this.controls.target)
    }
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
  }
}
