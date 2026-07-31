import type { Engine } from '@baidumap/mapv-three'
import * as THREE from 'three'
import type { RoadCoordinateProjector } from '../roadGeometry'
import {
  ROAD_ASPHALT_COLOR,
  ROAD_CURB_COLOR,
  ROAD_JUNCTION_COLOR,
  ROAD_SIDEWALK_COLOR,
  createAsphaltMaterial,
} from '../roadAppearance'
import { REALISTIC_INTERSECTION_SURFACE_Z } from '../sceneElevation'
import {
  loadIntersectionManifest,
  realisticIntersectionAssetUrl,
  type Point2,
  type RealisticConnection,
  type RealisticIntersectionManifest,
} from './intersectionManifest'
import {
  buildIntersectionApproachGeometry,
  pointAndTangent,
  SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS,
  signalPoleBase,
} from './intersectionApproachGeometry'
import {
  edgeCenterline,
  edgeRoadWidth,
  expandPolygon,
  junctionApronPoints,
  visualLanePoints,
} from './intersectionRoadGeometry'

interface SignalHeadMaterials {
  tlsId: string
  linkIndex: number
  red: THREE.MeshStandardMaterial
  yellow: THREE.MeshStandardMaterial
  green: THREE.MeshStandardMaterial
  redGlow: THREE.SpriteMaterial
  yellowGlow: THREE.SpriteMaterial
  greenGlow: THREE.SpriteMaterial
}

interface CachedIntersection {
  manifest: RealisticIntersectionManifest
  object: RealisticIntersectionObject
  usedAt: number
}

export interface RealisticSignalRuntimeState {
  intersection_id: string
  current_phase: number
  stage: string
}

const COLORS = {
  asphalt: ROAD_ASPHALT_COLOR,
  junction: ROAD_JUNCTION_COLOR,
  sidewalk: ROAD_SIDEWALK_COLOR,
  curb: ROAD_CURB_COLOR,
  bicycle: 0x655f55,
  marking: 0xf0eee3,
  yellow: 0xe1b63c,
  pole: 0x596164,
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

function makeGlowTexture(): THREE.CanvasTexture {
  return createCanvasTexture(96, 96, (context) => {
    const gradient = context.createRadialGradient(48, 48, 2, 48, 48, 46)
    gradient.addColorStop(0, 'rgba(255,255,255,0.98)')
    gradient.addColorStop(0.3, 'rgba(255,255,255,0.65)')
    gradient.addColorStop(1, 'rgba(255,255,255,0)')
    context.fillStyle = gradient
    context.fillRect(0, 0, 96, 96)
  })
}

function stripGeometry(
  points: Point2[],
  width: number,
  z: number,
  horizontalScale = 1,
): THREE.BufferGeometry {
  const positions: number[] = []
  const uvs: number[] = []
  const indices: number[] = []
  let distance = 0
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index]
    const previous = points[Math.max(0, index - 1)]
    const next = points[Math.min(points.length - 1, index + 1)]
    if (index > 0) distance += Math.hypot(point[0] - previous[0], point[1] - previous[1])
    const dx = next[0] - previous[0]
    const dy = next[1] - previous[1]
    const length = Math.hypot(dx, dy) || 1
    const nx = -dy / length
    const ny = dx / length
    positions.push(
      point[0] + nx * width / 2, point[1] + ny * width / 2, z,
      point[0] - nx * width / 2, point[1] - ny * width / 2, z,
    )
    uvs.push(0, distance / (8 * horizontalScale), 1, distance / (8 * horizontalScale))
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

function boxBetween(
  start: Point2,
  end: Point2,
  z: number,
  width: number,
  material: THREE.Material,
  height = width,
) {
  const length = Math.hypot(end[0] - start[0], end[1] - start[1])
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(length, width, height), material)
  mesh.position.set((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, z)
  mesh.rotation.z = Math.atan2(end[1] - start[1], end[0] - start[0])
  return mesh
}

function lineMesh(start: Point2, end: Point2, width: number, material: THREE.Material, z = 0.055) {
  const mesh = boxBetween(start, end, z, width, material, 0.025)
  mesh.renderOrder = 32
  return mesh
}

function offsetPolyline(points: Point2[], offset: number): Point2[] {
  return points.map((point, index) => {
    const previous = points[Math.max(0, index - 1)]
    const next = points[Math.min(points.length - 1, index + 1)]
    const dx = next[0] - previous[0]
    const dy = next[1] - previous[1]
    const length = Math.hypot(dx, dy) || 1
    return [point[0] - dy / length * offset, point[1] + dx / length * offset]
  })
}

function addPolylineSegments(
  group: THREE.Group,
  points: Point2[],
  width: number,
  material: THREE.Material,
  dashed: boolean,
): void {
  for (let index = 0; index < points.length - 1; index += 1) {
    if (dashed && index % 2 !== 0) continue
    group.add(lineMesh(points[index], points[index + 1], width, material))
  }
}

function polygonMesh(points: Point2[], material: THREE.Material, z: number, renderOrder: number): THREE.Mesh {
  const shape = new THREE.Shape(points.map(([x, y]) => new THREE.Vector2(x, y)))
  const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), material)
  mesh.position.z = z
  mesh.renderOrder = renderOrder
  return mesh
}

function verticalCylinder(radius: number, height: number, material: THREE.Material) {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius * 1.04, height, 16), material)
  mesh.rotation.x = Math.PI / 2
  return mesh
}

function setLens(
  material: THREE.MeshStandardMaterial,
  glow: THREE.SpriteMaterial,
  active: boolean,
  color: number,
) {
  material.color.setHex(active ? color : 0x171919)
  material.emissive.setHex(active ? color : 0x040404)
  material.emissiveIntensity = active ? 6 : 0.12
  glow.color.setHex(color)
  glow.opacity = active ? 0.72 : 0
}

function arrowShape(direction: RealisticConnection['direction']): THREE.Shape {
  const shape = new THREE.Shape()
  const points: Point2[] = direction === 'l'
    ? [[-0.2, -2.6], [0.2, -2.6], [0.2, 0.7], [-0.7, 0.7], [-0.7, 0.2], [-1.5, 1], [-0.7, 1.8], [-0.7, 1.3], [-0.2, 1.3]]
    : direction === 'r'
      ? [[0.2, -2.6], [-0.2, -2.6], [-0.2, 0.7], [0.7, 0.7], [0.7, 0.2], [1.5, 1], [0.7, 1.8], [0.7, 1.3], [0.2, 1.3]]
      : [[-0.2, -2.6], [0.2, -2.6], [0.2, 1], [0.7, 1], [0, 2.4], [-0.7, 1], [-0.2, 1]]
  shape.moveTo(...points[0])
  points.slice(1).forEach((point) => shape.lineTo(...point))
  shape.closePath()
  return shape
}

class RealisticIntersectionObject {
  readonly group = new THREE.Group()
  private readonly signalHeads: SignalHeadMaterials[] = []
  private readonly glowTexture = makeGlowTexture()

  constructor(readonly manifest: RealisticIntersectionManifest) {
    this.group.name = `realistic-intersection:${manifest.intersectionId}`
    this.group.renderOrder = 30
    this.buildRoads()
    this.buildSignals()
    this.updateSignalState(null)
  }

  private get horizontalScale(): number {
    return this.manifest.horizontalScale ?? 1
  }

  updateSignalState(runtime: RealisticSignalRuntimeState | null): void {
    const phase = runtime?.current_phase
    const stage = runtime?.stage?.toLowerCase()
    for (const head of this.signalHeads) {
      const templates = this.manifest.phaseTemplates ?? {}
      const phaseTemplates = phase == null
        ? undefined
        : templates[String(phase)] ?? templates[String(phase + 1)]
      const state = stage ? phaseTemplates?.[head.tlsId]?.[stage] : undefined
      const signal = state?.[head.linkIndex]?.toLowerCase() ?? 'r'
      setLens(head.red, head.redGlow, signal !== 'g' && signal !== 'y', 0xff3028)
      setLens(head.yellow, head.yellowGlow, signal === 'y', 0xffb51c)
      setLens(head.green, head.greenGlow, signal === 'g', 0x32ff78)
    }
  }

  dispose(): void {
    this.group.traverse((object) => {
      if (!(object instanceof THREE.Mesh || object instanceof THREE.Sprite)) return
      if (object instanceof THREE.Mesh) object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      for (const material of materials) {
        if ('map' in material) (material.map as THREE.Texture | null)?.dispose()
        material.dispose()
      }
    })
    this.glowTexture.dispose()
    this.group.clear()
  }

  private buildRoads(): void {
    const scale = this.horizontalScale
    const asphalt = createAsphaltMaterial(
      this.manifest.intersectionId.split('').reduce((sum, value) => sum + value.charCodeAt(0), 1),
      COLORS.asphalt,
    )
    const sidewalk = new THREE.MeshStandardMaterial({ color: COLORS.sidewalk, roughness: 0.92 })
    const curb = new THREE.MeshStandardMaterial({ color: COLORS.curb, roughness: 0.84 })
    const bicycle = new THREE.MeshStandardMaterial({ color: COLORS.bicycle, roughness: 0.9 })
    const white = new THREE.MeshStandardMaterial({
      color: COLORS.marking,
      roughness: 0.58,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    })
    const yellow = new THREE.MeshStandardMaterial({ color: COLORS.yellow, roughness: 0.62 })

    for (const edge of this.manifest.edges) {
      const centerline = edgeCenterline(edge)
      const roadWidth = edgeRoadWidth(edge)
      this.group.add(new THREE.Mesh(stripGeometry(centerline, roadWidth + 6.36 * scale, -0.075), sidewalk))
      this.group.add(new THREE.Mesh(stripGeometry(centerline, roadWidth + 0.36 * scale, -0.035), curb))
      this.group.add(new THREE.Mesh(stripGeometry(centerline, roadWidth + 0.03 * scale, 0, scale), asphalt))
      for (const lane of edge.lanes) {
        if (lane.kind !== 'bicycle') continue
        const laneSurface = new THREE.Mesh(stripGeometry(visualLanePoints(lane), Math.max(0.4 * scale, lane.width - 0.08 * scale), 0.018), bicycle)
        laneSurface.renderOrder = 30
        this.group.add(laneSurface)
      }
      for (let laneIndex = 0; laneIndex < edge.lanes.length - 1; laneIndex += 1) {
        const left = edge.lanes[laneIndex]
        const right = edge.lanes[laneIndex + 1]
        const leftPoints = visualLanePoints(left)
        const rightPoints = visualLanePoints(right)
        const count = Math.min(leftPoints.length, rightPoints.length)
        const ratio = left.width / (left.width + right.width)
        const boundary = Array.from({ length: count }, (_, index) => ([
          leftPoints[index][0] + (rightPoints[index][0] - leftPoints[index][0]) * ratio,
          leftPoints[index][1] + (rightPoints[index][1] - leftPoints[index][1]) * ratio,
        ] as Point2))
        addPolylineSegments(
          this.group,
          boundary,
          (left.kind === 'bicycle' || right.kind === 'bicycle' ? 0.14 : 0.1) * scale,
          white,
          left.kind === 'driving' && right.kind === 'driving',
        )
      }
      addPolylineSegments(this.group, offsetPolyline(centerline, roadWidth / 2), 0.12 * scale, white, false)
      addPolylineSegments(this.group, offsetPolyline(centerline, -roadWidth / 2), 0.12 * scale, white, false)
    }

    const apronPoints = junctionApronPoints(this.manifest.junctionShape, this.manifest.edges)
    const junctionMaterial = asphalt.clone()
    junctionMaterial.color.setHex(COLORS.junction)
    this.group.add(polygonMesh(expandPolygon(apronPoints, 3.18 * scale), sidewalk, -0.072, 26))
    this.group.add(polygonMesh(expandPolygon(apronPoints, 0.18 * scale), curb, -0.032, 27))
    this.group.add(polygonMesh(apronPoints, junctionMaterial, 0.012, 29))

    for (const edge of this.manifest.edges.filter((candidate) => candidate.incoming)) {
      const approach = buildIntersectionApproachGeometry(edge, scale, this.manifest.edges)
      if (!approach) continue
      const { tangent, normal, stopLineCenter, halfWidth } = approach
      this.group.add(lineMesh(
        [stopLineCenter[0] - normal[0] * halfWidth, stopLineCenter[1] - normal[1] * halfWidth],
        [stopLineCenter[0] + normal[0] * halfWidth, stopLineCenter[1] + normal[1] * halfWidth],
        0.42 * scale,
        white,
      ))
      for (const bar of approach.crosswalkBars) {
        this.group.add(lineMesh(
          [bar.center[0] - tangent[0] * bar.length / 2, bar.center[1] - tangent[1] * bar.length / 2],
          [bar.center[0] + tangent[0] * bar.length / 2, bar.center[1] + tangent[1] * bar.length / 2],
          bar.width,
          white,
        ))
      }
      for (const lane of edge.lanes.filter((candidate) => candidate.kind !== 'bicycle' && candidate.kind !== 'pedestrian')) {
        const connection = this.manifest.connections.find(
          (item) => item.fromEdge === edge.id && item.fromLane === lane.index,
        )
        if (!connection) continue
        const sample = pointAndTangent(lane, 25 * scale, true)
        const arrowGeometry = new THREE.ShapeGeometry(arrowShape(connection.direction))
        arrowGeometry.scale(scale, scale, 1)
        const arrow = new THREE.Mesh(arrowGeometry, white)
        arrow.position.set(sample.point[0], sample.point[1], 0.07)
        arrow.rotation.z = Math.atan2(sample.tangent[1], sample.tangent[0]) - Math.PI / 2
        arrow.renderOrder = 33
        this.group.add(arrow)
      }
      const guideStart: Point2 = [
        stopLineCenter[0] + normal[0] * (halfWidth + 0.15 * scale),
        stopLineCenter[1] + normal[1] * (halfWidth + 0.15 * scale),
      ]
      this.group.add(lineMesh(
        guideStart,
        [guideStart[0] - tangent[0] * 44 * scale, guideStart[1] - tangent[1] * 44 * scale],
        0.1 * scale,
        yellow,
      ))
    }
  }

  private buildSignals(): void {
    const scale = this.horizontalScale
    const poleMaterial = new THREE.MeshStandardMaterial({ color: COLORS.pole, roughness: 0.34, metalness: 0.72 })
    const housingMaterial = new THREE.MeshStandardMaterial({ color: 0x101416, roughness: 0.42, metalness: 0.24 })
    const visorMaterial = new THREE.MeshStandardMaterial({ color: 0x07090a, roughness: 0.5 })
    for (const edge of this.manifest.edges.filter((candidate) => candidate.incoming)) {
      const controlled = this.manifest.connections.filter((item) => item.fromEdge === edge.id)
      if (controlled.length === 0) continue
      const approach = buildIntersectionApproachGeometry(edge, scale, this.manifest.edges)
      if (!approach) continue
      const { tangent, normal, stopLineCenter: center, laneSamples: samples, outerSide: side } = approach
      const poleBase = signalPoleBase(approach, scale)
      const pole = verticalCylinder(0.17 * scale, 6.2, poleMaterial)
      pole.position.set(poleBase[0], poleBase[1], 3.1)
      this.group.add(pole)
      const armEnd: Point2 = [
        center[0] - normal[0] * 2.5 * scale * side - tangent[0] * SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS * scale,
        center[1] - normal[1] * 2.5 * scale * side - tangent[1] * SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS * scale,
      ]
      this.group.add(boxBetween(poleBase, armEnd, 5.9, 0.2 * scale, poleMaterial, 0.2))
      for (let laneIndex = 0; laneIndex < samples.length; laneIndex += 1) {
        const lane = samples[laneIndex].lane
        const connections = controlled.filter((item) => item.fromLane === lane.index)
        connections.forEach((connection, connectionIndex) => {
          const shift = (connectionIndex - (connections.length - 1) / 2) * 0.58 * scale
          const head = this.createSignalHead(connection, housingMaterial, visorMaterial)
          head.position.set(
            samples[laneIndex].point[0] + normal[0] * shift,
            samples[laneIndex].point[1] + normal[1] * shift,
            5.56,
          )
          head.rotation.z = Math.atan2(-tangent[0], tangent[1])
          this.group.add(head)
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
    group.scale.set(this.horizontalScale, this.horizontalScale, 1)
    group.add(new THREE.Mesh(new THREE.BoxGeometry(0.64, 0.32, 1.75), housingMaterial))
    const colors = [0xff3028, 0xffb51c, 0x32ff78]
    const materials = colors.map((color) => new THREE.MeshStandardMaterial({
      color: 0x171919,
      emissive: color,
      emissiveIntensity: 0.12,
      roughness: 0.55,
      toneMapped: false,
    }))
    const glows = colors.map((color) => new THREE.SpriteMaterial({
      map: this.glowTexture,
      color,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      toneMapped: false,
    }))
    ;[0.57, 0, -0.57].forEach((z, index) => {
      const lens = new THREE.Mesh(new THREE.SphereGeometry(0.2, 20, 12), materials[index])
      lens.scale.y = 0.42
      lens.position.set(0, -0.2, z)
      group.add(lens)
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.225, 0.028, 8, 20), visorMaterial)
      ring.rotation.x = Math.PI / 2
      ring.position.set(0, -0.22, z)
      group.add(ring)
      const glow = new THREE.Sprite(glows[index])
      glow.position.set(0, -0.31, z)
      glow.scale.setScalar(0.75)
      group.add(glow)
    })
    this.signalHeads.push({
      tlsId: connection.tlsId,
      linkIndex: connection.linkIndex,
      red: materials[0],
      yellow: materials[1],
      green: materials[2],
      redGlow: glows[0],
      yellowGlow: glows[1],
      greenGlow: glows[2],
    })
    return group
  }
}

export class MapvRealisticIntersectionLayer {
  private readonly cache = new Map<string, CachedIntersection>()
  private activeId: string | null = null
  private requestVersion = 0

  constructor(
    private readonly engine: Engine,
    private readonly projector: RoadCoordinateProjector,
    private readonly cacheLimit = 3,
  ) {}

  get activeIntersectionId(): string | null {
    return this.activeId
  }

  async prepare(intersectionId: string): Promise<RealisticIntersectionManifest> {
    const version = ++this.requestVersion
    let cached = this.cache.get(intersectionId)
    if (!cached) {
      const manifest = await loadIntersectionManifest(realisticIntersectionAssetUrl(intersectionId))
      if (version !== this.requestVersion) throw new DOMException('Stale intersection request', 'AbortError')
      const object = new RealisticIntersectionObject(manifest)
      const projected = this.projector([manifest.origin.longitude, manifest.origin.latitude, 0])
      const geographicPosition = projected[2] == null
        ? [projected[0], projected[1]]
        : [projected[0], projected[1], projected[2]]
      const scenePosition = this.engine.map.projectArrayCoordinate(geographicPosition)
      object.group.position.set(
        scenePosition[0],
        scenePosition[1],
        (scenePosition[2] ?? 0) + REALISTIC_INTERSECTION_SURFACE_Z,
      )
      object.group.visible = false
      this.engine.add(object.group)
      cached = { manifest, object, usedAt: performance.now() }
      this.cache.set(intersectionId, cached)
    }
    if (version !== this.requestVersion) throw new DOMException('Stale intersection request', 'AbortError')
    cached.usedAt = performance.now()
    return cached.manifest
  }

  activate(intersectionId: string): RealisticIntersectionManifest {
    const cached = this.cache.get(intersectionId)
    if (!cached) throw new Error(`Intersection ${intersectionId} has not been prepared`)
    const previous = this.activeId ? this.cache.get(this.activeId) : null
    cached.object.group.visible = true
    if (previous && previous !== cached) previous.object.group.visible = false
    this.activeId = intersectionId
    this.trimCache()
    this.engine.requestRender()
    return cached.manifest
  }

  async switchTo(intersectionId: string): Promise<RealisticIntersectionManifest> {
    await this.prepare(intersectionId)
    return this.activate(intersectionId)
  }

  updateSignals(intersections: RealisticSignalRuntimeState[] | null): void {
    if (!this.activeId) return
    const cached = this.cache.get(this.activeId)
    const runtime = intersections?.find((item) => item.intersection_id === this.activeId) ?? null
    cached?.object.updateSignalState(runtime)
    this.engine.requestRender()
  }

  destroy(): void {
    this.requestVersion += 1
    for (const cached of this.cache.values()) {
      this.engine.remove(cached.object.group)
      cached.object.dispose()
    }
    this.cache.clear()
    this.activeId = null
  }

  private trimCache(): void {
    const candidates = [...this.cache.entries()]
      .filter(([id]) => id !== this.activeId)
      .sort((left, right) => left[1].usedAt - right[1].usedAt)
    while (this.cache.size > this.cacheLimit && candidates.length > 0) {
      const [id, cached] = candidates.shift()!
      this.engine.remove(cached.object.group)
      cached.object.dispose()
      this.cache.delete(id)
    }
  }
}
