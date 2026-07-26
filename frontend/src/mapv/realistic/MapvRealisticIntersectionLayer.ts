import type { Engine } from '@baidumap/mapv-three'
import * as THREE from 'three'
import type { RoadCoordinateProjector } from '../roadGeometry'
import {
  loadIntersectionManifest,
  realisticIntersectionAssetUrl,
  type Point2,
  type RealisticConnection,
  type RealisticIntersectionManifest,
  type RealisticLane,
} from './intersectionManifest'

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
  asphalt: 0x515454,
  junction: 0x494c4c,
  shoulder: 0x777b7a,
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

function makeAsphaltTexture(seedValue: number): THREE.CanvasTexture {
  const texture = createCanvasTexture(256, 256, (context) => {
    context.fillStyle = '#444747'
    context.fillRect(0, 0, 256, 256)
    let seed = Math.max(1, seedValue)
    for (let index = 0; index < 5200; index += 1) {
      seed = (seed * 16807) % 2147483647
      const x = seed % 256
      seed = (seed * 16807) % 2147483647
      const y = seed % 256
      seed = (seed * 16807) % 2147483647
      const shade = 46 + (seed % 35)
      context.fillStyle = `rgba(${shade},${shade},${shade},0.32)`
      context.fillRect(x, y, 1, 1)
    }
  })
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(0.65, 0.65)
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

function pointAndTangent(lane: RealisticLane, distance: number, incoming: boolean) {
  const points = lane.points
  let index = incoming ? points.length - 1 : 0
  const step = incoming ? -1 : 1
  let remaining = distance
  let current = points[index]
  while (index + step >= 0 && index + step < points.length) {
    const next = points[index + step]
    const segmentLength = Math.hypot(next[0] - current[0], next[1] - current[1])
    if (segmentLength >= remaining) {
      const ratio = remaining / segmentLength
      const point: Point2 = [
        current[0] + (next[0] - current[0]) * ratio,
        current[1] + (next[1] - current[1]) * ratio,
      ]
      const tangent: Point2 = incoming
        ? [(current[0] - next[0]) / segmentLength, (current[1] - next[1]) / segmentLength]
        : [(next[0] - current[0]) / segmentLength, (next[1] - current[1]) / segmentLength]
      return { point, tangent }
    }
    remaining -= segmentLength
    current = next
    index += step
  }
  return { point: current, tangent: [1, 0] as Point2 }
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
    const asphaltTexture = makeAsphaltTexture(
      this.manifest.intersectionId.split('').reduce((sum, value) => sum + value.charCodeAt(0), 1),
    )
    const asphalt = new THREE.MeshStandardMaterial({
      color: COLORS.asphalt,
      map: asphaltTexture,
      roughness: 0.94,
      metalness: 0.02,
    })
    const shoulder = new THREE.MeshStandardMaterial({ color: COLORS.shoulder, roughness: 0.9 })
    const white = new THREE.MeshStandardMaterial({
      color: COLORS.marking,
      roughness: 0.58,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    })
    const yellow = new THREE.MeshStandardMaterial({ color: COLORS.yellow, roughness: 0.62 })

    for (const edge of this.manifest.edges) {
      for (const lane of edge.lanes) {
        this.group.add(new THREE.Mesh(stripGeometry(lane.points, lane.width + 0.44 * scale, -0.06), shoulder))
        this.group.add(new THREE.Mesh(stripGeometry(lane.points, lane.width + 0.08 * scale, 0, scale), asphalt))
      }
      for (let laneIndex = 0; laneIndex < edge.lanes.length - 1; laneIndex += 1) {
        const left = edge.lanes[laneIndex]
        const right = edge.lanes[laneIndex + 1]
        const count = Math.min(left.points.length, right.points.length)
        for (let index = 0; index < count - 1; index += 1) {
          if (index % 2 !== 0) continue
          const start: Point2 = [
            (left.points[index][0] + right.points[index][0]) / 2,
            (left.points[index][1] + right.points[index][1]) / 2,
          ]
          const end: Point2 = [
            (left.points[index + 1][0] + right.points[index + 1][0]) / 2,
            (left.points[index + 1][1] + right.points[index + 1][1]) / 2,
          ]
          this.group.add(lineMesh(start, end, 0.11 * scale, white))
        }
      }
    }

    const junctionShape = new THREE.Shape(
      this.manifest.junctionShape.map(([x, y]) => new THREE.Vector2(x, y)),
    )
    const junctionMaterial = asphalt.clone()
    junctionMaterial.color.setHex(COLORS.junction)
    const junction = new THREE.Mesh(new THREE.ShapeGeometry(junctionShape), junctionMaterial)
    junction.position.z = 0.012
    junction.renderOrder = 29
    this.group.add(junction)

    for (const edge of this.manifest.edges.filter((candidate) => candidate.incoming)) {
      const laneSamples = edge.lanes.map((lane) => pointAndTangent(lane, 6 * scale, true))
      if (laneSamples.length === 0) continue
      const tangent = laneSamples[0].tangent
      const normal: Point2 = [-tangent[1], tangent[0]]
      const projected = laneSamples.map(({ point }) => point[0] * normal[0] + point[1] * normal[1])
      const min = Math.min(...projected) - edge.lanes[0].width / 2
      const max = Math.max(...projected) + edge.lanes[edge.lanes.length - 1].width / 2
      const centerAlong = (min + max) / 2
      const center = laneSamples[Math.floor(laneSamples.length / 2)].point
      const centerProjection = center[0] * normal[0] + center[1] * normal[1]
      const adjusted: Point2 = [
        center[0] + normal[0] * (centerAlong - centerProjection),
        center[1] + normal[1] * (centerAlong - centerProjection),
      ]
      const halfWidth = (max - min) / 2
      this.group.add(lineMesh(
        [adjusted[0] - normal[0] * halfWidth, adjusted[1] - normal[1] * halfWidth],
        [adjusted[0] + normal[0] * halfWidth, adjusted[1] + normal[1] * halfWidth],
        0.42 * scale,
        white,
      ))
      for (let stripe = -4; stripe <= 4; stripe += 1) {
        const offset = (11 + stripe * 0.72) * scale
        const stripeCenter: Point2 = [adjusted[0] - tangent[0] * offset, adjusted[1] - tangent[1] * offset]
        this.group.add(lineMesh(
          [stripeCenter[0] - normal[0] * (halfWidth + 2 * scale), stripeCenter[1] - normal[1] * (halfWidth + 2 * scale)],
          [stripeCenter[0] + normal[0] * (halfWidth + 2 * scale), stripeCenter[1] + normal[1] * (halfWidth + 2 * scale)],
          0.38 * scale,
          white,
        ))
      }
      for (const lane of edge.lanes) {
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
        adjusted[0] + normal[0] * (halfWidth + 0.15 * scale),
        adjusted[1] + normal[1] * (halfWidth + 0.15 * scale),
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
      const samples = edge.lanes.map((lane) => pointAndTangent(lane, 8.5 * scale, true))
      const tangent = samples[0].tangent
      const normal: Point2 = [-tangent[1], tangent[0]]
      const center: Point2 = [
        samples.reduce((sum, item) => sum + item.point[0], 0) / samples.length,
        samples.reduce((sum, item) => sum + item.point[1], 0) / samples.length,
      ]
      const side = center[0] * normal[0] + center[1] * normal[1] >= 0 ? 1 : -1
      const poleBase: Point2 = [
        center[0] + normal[0] * 7 * scale * side,
        center[1] + normal[1] * 7 * scale * side,
      ]
      const pole = verticalCylinder(0.17 * scale, 6.2, poleMaterial)
      pole.position.set(poleBase[0], poleBase[1], 3.1)
      this.group.add(pole)
      const armEnd: Point2 = [
        center[0] - normal[0] * 2.5 * scale * side,
        center[1] - normal[1] * 2.5 * scale * side,
      ]
      this.group.add(boxBetween(poleBase, armEnd, 5.9, 0.2 * scale, poleMaterial, 0.2))
      for (let laneIndex = 0; laneIndex < edge.lanes.length; laneIndex += 1) {
        const lane = edge.lanes[laneIndex]
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

  async switchTo(intersectionId: string): Promise<RealisticIntersectionManifest> {
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
      object.group.position.set(scenePosition[0], scenePosition[1], (scenePosition[2] ?? 0) + 1.04)
      object.group.visible = false
      this.engine.add(object.group)
      cached = { manifest, object, usedAt: performance.now() }
      this.cache.set(intersectionId, cached)
    }
    if (version !== this.requestVersion) throw new DOMException('Stale intersection request', 'AbortError')
    const previous = this.activeId ? this.cache.get(this.activeId) : null
    cached.usedAt = performance.now()
    cached.object.group.visible = true
    if (previous && previous !== cached) previous.object.group.visible = false
    this.activeId = intersectionId
    this.trimCache()
    this.engine.requestRender()
    return cached.manifest
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
