import type { Engine } from '@baidumap/mapv-three'
import * as THREE from 'three'
import type { RoadCoordinateProjector } from '../roadGeometry'
import { distanceMeters as geographicDistanceMeters } from '../vehicleVisibility'
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
  type RoadSurfacePolygon,
} from './intersectionManifest'
import {
  buildCollisionFreeIntersectionApproaches,
  SIGNAL_POLE_LONGITUDINAL_SETBACK_METERS,
  signalPoleBase,
  type PositionedIntersectionApproach,
} from './intersectionApproachGeometry'
import {
  edgeCenterline,
  edgeRoadWidth,
  expandPolygon,
  junctionApronPoints,
  nearestPolylineProgress,
  samplePolyline,
  visualLanePoints,
} from './intersectionRoadGeometry'
import {
  resolveIntersectionRoadLod,
  type IntersectionRoadLod,
} from './intersectionLod'
import {
  buildRoadTransitionSections,
  roadBoundaryFadeFlags,
} from './roadTransition'
import {
  LANE_ARROW_MAX_VISIBLE_RANGE_METERS,
  LANE_ARROW_RENDER_ORDER,
  LANE_ARROW_SURFACE_Z,
  buildLaneDirectionArrows,
  createLaneArrowGeometry,
  createLaneArrowMaterial,
  laneArrowsAvailableForLod,
  type LaneArrowPattern,
} from './laneDirectionArrows'
import {
  surfaceOffsetIsExcluded,
  visiblePolylineSections,
} from './roadSurfaceExclusions'

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

interface CachedOverviewIntersection {
  manifest: RealisticIntersectionManifest
  object: RealisticIntersectionOverviewObject
}

interface CachedMediumIntersection {
  manifest: RealisticIntersectionManifest
  object: RealisticIntersectionObject
  usedAt: number
}

interface IntersectionViewport {
  center: readonly number[]
  rangeMeters: number
}

export interface RealisticSignalRuntimeState {
  intersection_id: string
  current_phase: number
  stage: string
}

export interface RealisticRuntimeDisturbance {
  eventId: string
  intersectionId: string
  eventType: string
  state: string
  laneIds: string[]
  positionRatio?: number
}

export interface RealisticLaneScenePosition {
  scene: [number, number, number]
  mapCoordinate: [number, number, number]
  laneId: string
  positionRatio: number
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

function addTransitionStrip(
  group: THREE.Group,
  points: Point2[],
  width: number,
  z: number,
  material: THREE.Material,
  radiusSceneUnits: number,
  transitionLength: number,
  horizontalScale = 1,
  renderOrder = 0,
  exclusions?: import('./intersectionManifest').RealisticRoadSurfaceExclusion[],
  referencePoints: Point2[] = points,
): void {
  for (const visiblePoints of visiblePolylineSections(points, exclusions, horizontalScale, referencePoints)) {
    const { fadeStart, fadeEnd } = roadBoundaryFadeFlags(visiblePoints, radiusSceneUnits)
    const sections = buildRoadTransitionSections(
      visiblePoints,
      fadeStart,
      fadeEnd,
      transitionLength,
    )
    for (const item of sections) {
      const sectionMaterial = item.opacity >= 0.999
        ? material
        : material.clone()
      if (sectionMaterial !== material) {
        sectionMaterial.opacity = item.opacity
        sectionMaterial.transparent = true
        sectionMaterial.depthWrite = false
      }
      const mesh = new THREE.Mesh(
        stripGeometry(item.points, width, z, horizontalScale),
        sectionMaterial,
      )
      mesh.renderOrder = renderOrder
      group.add(mesh)
    }
  }
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

function polygonMesh(
  polygon: Point2[] | RoadSurfacePolygon,
  material: THREE.Material,
  z: number,
  renderOrder: number,
): THREE.Mesh {
  const outer = Array.isArray(polygon) ? polygon : polygon.outer
  const shape = new THREE.Shape(outer.map(([x, y]) => new THREE.Vector2(x, y)))
  if (!Array.isArray(polygon)) {
    for (const hole of polygon.holes ?? []) {
      shape.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y))))
    }
  }
  const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), material)
  mesh.position.z = z
  mesh.renderOrder = renderOrder
  return mesh
}

function addRoadJointMeshes(
  group: THREE.Group,
  manifest: RealisticIntersectionManifest,
  materials: { sidewalk?: THREE.Material; curb?: THREE.Material; asphalt: THREE.Material },
): boolean {
  const joints = manifest.roadJoints ?? []
  if (joints.length === 0) return false
  for (const joint of joints) {
    if (joint.surfaceHidden) continue
    const parts = joint.surfaceParts
    if (materials.sidewalk) {
      for (const polygon of parts?.sidewalk ?? [joint.polygons.sidewalk]) {
        group.add(polygonMesh(polygon, materials.sidewalk, -0.072, 26))
      }
    }
    if (materials.curb) {
      for (const polygon of parts?.curb ?? [joint.polygons.curb]) {
        group.add(polygonMesh(polygon, materials.curb, -0.032, 27))
      }
    }
    for (const polygon of parts?.asphalt ?? [joint.polygons.asphalt]) {
      group.add(polygonMesh(polygon, materials.asphalt, 0.014, 29))
    }
  }
  return true
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

class RealisticIntersectionObject {
  readonly group = new THREE.Group()
  private readonly arrowGroup = new THREE.Group()
  private readonly disturbanceGroup = new THREE.Group()
  private readonly signalHeads: SignalHeadMaterials[] = []
  private readonly glowTexture = makeGlowTexture()
  private readonly approaches: PositionedIntersectionApproach[]

  constructor(
    readonly manifest: RealisticIntersectionManifest,
    detail: 'medium' | 'full' = 'full',
  ) {
    this.approaches = buildCollisionFreeIntersectionApproaches(
      manifest.edges,
      manifest.horizontalScale ?? 1,
    )
    this.group.name = `realistic-intersection-${detail}:${manifest.intersectionId}`
    this.group.renderOrder = 30
    this.arrowGroup.name = `lane-direction-arrows:${manifest.intersectionId}:${detail}`
    this.disturbanceGroup.name = `runtime-disturbances:${manifest.intersectionId}:${detail}`
    this.group.add(this.arrowGroup)
    this.group.add(this.disturbanceGroup)
    this.buildRoads(detail === 'full', laneArrowsAvailableForLod(detail))
    if (detail === 'full') this.buildSignals()
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

  setArrowsVisible(visible: boolean): void {
    this.arrowGroup.visible = visible
  }

  updateRuntimeDisturbances(events: readonly RealisticRuntimeDisturbance[]): void {
    this.clearRuntimeDisturbances()
    const lanes = new Map(this.manifest.edges.flatMap((edge) => (
      edge.lanes.map((lane) => [lane.id, lane] as const)
    )))
    for (const event of events) {
      if (event.state !== 'ACTIVE') continue
      const color = event.eventType === 'speed_limit' ? 0xff8a00 : 0xff243f
      const material = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: event.eventType === 'speed_limit' ? 0.48 : 0.58,
        depthWrite: false,
        side: THREE.DoubleSide,
      })
      let materialUsed = false
      for (const laneId of event.laneIds) {
        const lane = lanes.get(laneId)
        if (!lane) continue
        const points = visualLanePoints(lane)
        if (points.length < 2) continue
        if (event.eventType === 'accident') {
          const progress = Number.isFinite(event.positionRatio)
            ? Math.max(0, Math.min(1, Number(event.positionRatio)))
            : 0.5
          const point = samplePolyline(points, progress)
          const marker = new THREE.Mesh(
            new THREE.RingGeometry(1.4 * this.horizontalScale, 2.4 * this.horizontalScale, 20),
            material,
          )
          marker.position.set(point[0], point[1], 0.14)
          marker.renderOrder = 55
          this.disturbanceGroup.add(marker)
          materialUsed = true
          continue
        }
        const overlay = new THREE.Mesh(
          stripGeometry(
            points,
            Math.max(1.2 * this.horizontalScale, lane.width * 0.72),
            0.11,
            this.horizontalScale,
          ),
          material,
        )
        overlay.renderOrder = 54
        this.disturbanceGroup.add(overlay)
        materialUsed = true
      }
      if (!materialUsed) material.dispose()
    }
  }

  private clearRuntimeDisturbances(): void {
    const materials = new Set<THREE.Material>()
    for (const child of [...this.disturbanceGroup.children]) {
      if (child instanceof THREE.Mesh) {
        child.geometry.dispose()
        const values = Array.isArray(child.material) ? child.material : [child.material]
        values.forEach((material) => materials.add(material))
      }
      this.disturbanceGroup.remove(child)
    }
    materials.forEach((material) => material.dispose())
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

  private buildRoads(includeFineMarkings: boolean, includeDirectionArrows: boolean): void {
    const scale = this.horizontalScale
    const radiusSceneUnits = this.manifest.radiusSceneUnits ?? this.manifest.radiusMeters * scale
    const transitionLength = 20 * scale
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
      addTransitionStrip(
        this.group,
        centerline,
        roadWidth + 6.36 * scale,
        -0.075,
        sidewalk,
        radiusSceneUnits,
        transitionLength,
        1,
        26,
        edge.surfaceExclusions,
      )
      addTransitionStrip(
        this.group,
        centerline,
        roadWidth + 0.36 * scale,
        -0.035,
        curb,
        radiusSceneUnits,
        transitionLength,
        1,
        27,
        edge.surfaceExclusions,
      )
      addTransitionStrip(
        this.group,
        centerline,
        roadWidth + 0.03 * scale,
        0,
        asphalt,
        radiusSceneUnits,
        transitionLength,
        scale,
        28,
        edge.surfaceExclusions,
      )
      for (const lane of edge.lanes) {
        if (lane.kind !== 'bicycle') continue
        for (const points of visiblePolylineSections(
          visualLanePoints(lane),
          edge.surfaceExclusions,
          scale,
          centerline,
        )) {
          const laneSurface = new THREE.Mesh(stripGeometry(points, Math.max(0.4 * scale, lane.width - 0.08 * scale), 0.018), bicycle)
          laneSurface.renderOrder = 30
          this.group.add(laneSurface)
        }
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
        for (const points of visiblePolylineSections(boundary, edge.surfaceExclusions, scale, centerline)) {
          addPolylineSegments(
            this.group,
            points,
            (left.kind === 'bicycle' || right.kind === 'bicycle' ? 0.14 : 0.1) * scale,
            white,
            left.kind === 'driving' && right.kind === 'driving',
          )
        }
      }
      for (const offset of [roadWidth / 2, -roadWidth / 2]) {
        for (const points of visiblePolylineSections(
          offsetPolyline(centerline, offset),
          edge.surfaceExclusions,
          scale,
          centerline,
        )) {
          addPolylineSegments(this.group, points, 0.12 * scale, white, false)
        }
      }
    }

    if (!addRoadJointMeshes(this.group, this.manifest, { sidewalk, curb, asphalt })) {
      const apronPoints = junctionApronPoints(this.manifest.junctionShape, this.manifest.edges)
      const junctionMaterial = asphalt.clone()
      junctionMaterial.color.setHex(COLORS.junction)
      this.group.add(polygonMesh(expandPolygon(apronPoints, 3.18 * scale), sidewalk, -0.072, 26))
      this.group.add(polygonMesh(expandPolygon(apronPoints, 0.18 * scale), curb, -0.032, 27))
      this.group.add(polygonMesh(apronPoints, junctionMaterial, 0.012, 29))
    }

    if (includeDirectionArrows) this.buildDirectionArrows()

    for (const { geometry: approach } of this.approaches) {
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
      if (includeFineMarkings) {
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
  }

  private buildDirectionArrows(): void {
    const edges = new Map(this.manifest.edges.map((edge) => [edge.id, edge]))
    const arrows = buildLaneDirectionArrows(this.manifest).filter((arrow) => {
      const edge = edges.get(arrow.edgeId)
      if (!edge?.surfaceExclusions?.length) return true
      const centerline = edgeCenterline(edge)
      const nearest = nearestPolylineProgress(arrow.point, centerline)
      const lengthSceneUnits = centerline.slice(1).reduce((sum, point, index) => (
        sum + Math.hypot(point[0] - centerline[index][0], point[1] - centerline[index][1])
      ), 0)
      return !nearest || !surfaceOffsetIsExcluded(
        nearest.progress * lengthSceneUnits / this.horizontalScale,
        edge.surfaceExclusions,
        lengthSceneUnits / this.horizontalScale,
      )
    })
    const byPattern = new Map<LaneArrowPattern, typeof arrows>()
    for (const arrow of arrows) {
      byPattern.set(arrow.pattern, [...(byPattern.get(arrow.pattern) ?? []), arrow])
    }
    const transform = new THREE.Object3D()
    for (const [pattern, instances] of byPattern) {
      const mesh = new THREE.InstancedMesh(
        createLaneArrowGeometry(pattern),
        createLaneArrowMaterial(),
        instances.length,
      )
      mesh.name = `lane-direction-arrow:${pattern}`
      mesh.renderOrder = LANE_ARROW_RENDER_ORDER
      instances.forEach((arrow, index) => {
        transform.position.set(arrow.point[0], arrow.point[1], LANE_ARROW_SURFACE_Z)
        transform.rotation.set(0, 0, arrow.headingRadians)
        transform.scale.setScalar(arrow.scale)
        transform.updateMatrix()
        mesh.setMatrixAt(index, transform.matrix)
      })
      mesh.instanceMatrix.needsUpdate = true
      mesh.computeBoundingBox()
      mesh.computeBoundingSphere()
      this.arrowGroup.add(mesh)
    }
  }

  private buildSignals(): void {
    const scale = this.horizontalScale
    const poleMaterial = new THREE.MeshStandardMaterial({ color: COLORS.pole, roughness: 0.34, metalness: 0.72 })
    const housingMaterial = new THREE.MeshStandardMaterial({ color: 0x101416, roughness: 0.42, metalness: 0.24 })
    const visorMaterial = new THREE.MeshStandardMaterial({ color: 0x07090a, roughness: 0.5 })
    for (const { edge, geometry: approach } of this.approaches) {
      const controlled = this.manifest.connections.filter((item) => item.fromEdge === edge.id)
      if (controlled.length === 0) continue
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

class RealisticIntersectionOverviewObject {
  readonly group = new THREE.Group()

  constructor(readonly manifest: RealisticIntersectionManifest) {
    const scale = manifest.horizontalScale ?? 1
    const radiusSceneUnits = manifest.radiusSceneUnits ?? manifest.radiusMeters * scale
    const transitionLength = 20 * scale
    const asphalt = createAsphaltMaterial(
      manifest.intersectionId.split('').reduce((sum, value) => sum + value.charCodeAt(0), 1),
      COLORS.asphalt,
    )
    this.group.name = `realistic-intersection-overview:${manifest.intersectionId}`
    this.group.renderOrder = 24
    for (const edge of manifest.edges) {
      addTransitionStrip(
        this.group,
        edgeCenterline(edge),
        edgeRoadWidth(edge) + 0.04 * scale,
        0,
        asphalt,
        radiusSceneUnits,
        transitionLength,
        scale,
        24,
        edge.surfaceExclusions,
      )
    }
    if (!addRoadJointMeshes(this.group, manifest, { asphalt })) {
      const junction = asphalt.clone()
      junction.color.setHex(COLORS.junction)
      this.group.add(polygonMesh(
        junctionApronPoints(manifest.junctionShape, manifest.edges),
        junction,
        0.012,
        25,
      ))
    }
  }

  dispose(): void {
    const materials = new Set<THREE.Material>()
    this.group.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return
      object.geometry.dispose()
      const values = Array.isArray(object.material) ? object.material : [object.material]
      values.forEach((material) => materials.add(material))
    })
    materials.forEach((material) => material.dispose())
    this.group.clear()
  }
}

export class MapvRealisticIntersectionLayer {
  private readonly cache = new Map<string, CachedIntersection>()
  private readonly mediumCache = new Map<string, CachedMediumIntersection>()
  private readonly overviewCache = new Map<string, CachedOverviewIntersection>()
  private readonly manifests = new Map<string, RealisticIntersectionManifest>()
  private readonly manifestRequests = new Map<string, Promise<RealisticIntersectionManifest>>()
  private readonly detailRequests = new Map<string, Promise<RealisticIntersectionManifest>>()
  private readonly detailConsumers = new Map<string, Set<AbortSignal>>()
  private readonly pendingActivations = new Set<string>()
  private readonly lodById = new Map<string, IntersectionRoadLod>()
  private readonly runtimeDisturbances = new Map<string, RealisticRuntimeDisturbance[]>()
  private readonly runtimeDisturbanceSignatures = new Map<string, string>()
  private lastViewport: IntersectionViewport | null = null
  private activeId: string | null = null
  private interactionActive = false

  constructor(
    private readonly engine: Engine,
    private readonly projector: RoadCoordinateProjector,
    private readonly cacheLimit = 2,
    private readonly mediumCacheLimit = 4,
  ) {}

  get activeIntersectionId(): string | null {
    return this.activeId
  }

  resolveLaneScenePosition(
    intersectionId: string,
    laneId: string,
    positionRatio = 0.5,
  ): RealisticLaneScenePosition | null {
    const manifest = this.manifests.get(intersectionId)
    if (!manifest) return null
    const lane = manifest.edges.flatMap((edge) => edge.lanes).find((candidate) => candidate.id === laneId)
    if (!lane) return null
    const ratio = Number.isFinite(positionRatio) ? Math.max(0, Math.min(1, positionRatio)) : 0.5
    const point = samplePolyline(visualLanePoints(lane), ratio)
    const origin = this.projector([manifest.origin.longitude, manifest.origin.latitude, 0])
    const originCoordinate: [number, number, number] = [origin[0], origin[1], origin[2] ?? 0]
    const originScene = this.engine.map.projectArrayCoordinate(originCoordinate)
    const scene: [number, number, number] = [
      originScene[0] + point[0],
      originScene[1] + point[1],
      (originScene[2] ?? 0) + REALISTIC_INTERSECTION_SURFACE_Z + 0.18,
    ]
    const mapPosition = this.engine.map.unprojectArrayCoordinate(scene)
    return {
      scene,
      mapCoordinate: [mapPosition[0], mapPosition[1], mapPosition[2] ?? scene[2]],
      laneId,
      positionRatio: ratio,
    }
  }

  resolveLaneScenePositionAny(
    laneId: string,
    positionRatio = 0.5,
    preferredIntersectionId = '',
  ): (RealisticLaneScenePosition & { intersectionId: string }) | null {
    const intersectionIds = preferredIntersectionId
      ? [preferredIntersectionId, ...this.manifests.keys()].filter(
          (value, index, values) => values.indexOf(value) === index,
        )
      : [...this.manifests.keys()]
    for (const intersectionId of intersectionIds) {
      const position = this.resolveLaneScenePosition(intersectionId, laneId, positionRatio)
      if (position) return { ...position, intersectionId }
    }
    return null
  }

  setInteractionActive(active: boolean): void {
    this.interactionActive = active
  }

  async prepareOverview(intersectionIds: string[]): Promise<void> {
    const queue = [...new Set(intersectionIds)]
    const worker = async () => {
      while (queue.length > 0) {
        const intersectionId = queue.shift()
        if (!intersectionId || this.overviewCache.has(intersectionId)) continue
        const manifest = await this.loadManifest(intersectionId)
        const overviewObject = new RealisticIntersectionOverviewObject(manifest)
        this.placeAtIntersection(overviewObject.group, manifest)
        overviewObject.group.visible = intersectionId !== this.activeId
        this.engine.add(overviewObject.group)
        this.overviewCache.set(intersectionId, { manifest, object: overviewObject })
        this.engine.requestRender()
      }
    }
    await Promise.all(Array.from({ length: Math.min(4, queue.length) }, worker))
    if (this.lastViewport) this.refreshViewport(this.lastViewport.center, this.lastViewport.rangeMeters)
  }

  async prepare(
    intersectionId: string,
    signal?: AbortSignal,
  ): Promise<RealisticIntersectionManifest> {
    if (signal?.aborted) throw new DOMException('Intersection preparation aborted', 'AbortError')
    if (signal) {
      const consumers = this.detailConsumers.get(intersectionId) ?? new Set<AbortSignal>()
      consumers.add(signal)
      this.detailConsumers.set(intersectionId, consumers)
    }
    try {
    let cached = this.cache.get(intersectionId)
    if (cached) {
      cached.usedAt = performance.now()
      return cached.manifest
    }
    let request = this.detailRequests.get(intersectionId)
    if (!request) {
      request = (async () => {
        const manifest = await this.loadManifest(intersectionId)
        const object = new RealisticIntersectionObject(manifest, 'full')
        object.updateRuntimeDisturbances(this.runtimeDisturbances.get(intersectionId) ?? [])
        this.placeAtIntersection(object.group, manifest)
        object.group.visible = false
        this.engine.add(object.group)
        const detail = { manifest, object, usedAt: performance.now() }
        this.cache.set(intersectionId, detail)
        if (this.lastViewport) this.refreshViewport(this.lastViewport.center, this.lastViewport.rangeMeters)
        else if (intersectionId === this.activeId) object.group.visible = true
        this.trimCache()
        this.engine.requestRender()
        return manifest
      })().finally(() => this.detailRequests.delete(intersectionId))
      this.detailRequests.set(intersectionId, request)
    }
    const manifest = await request
    if (signal?.aborted) {
      const hasCurrentConsumer = [...(this.detailConsumers.get(intersectionId) ?? [])]
        .some((consumer) => consumer !== signal && !consumer.aborted)
      if (!hasCurrentConsumer) this.discard(intersectionId)
      throw new DOMException('Intersection preparation aborted', 'AbortError')
    }
    return manifest
    } finally {
      if (signal) {
        const consumers = this.detailConsumers.get(intersectionId)
        consumers?.delete(signal)
        if (consumers?.size === 0) this.detailConsumers.delete(intersectionId)
      }
    }
  }

  discard(intersectionId: string): void {
    if (intersectionId === this.activeId || this.pendingActivations.has(intersectionId)) return
    const cached = this.cache.get(intersectionId)
    if (!cached) return
    this.engine.remove(cached.object.group)
    cached.object.dispose()
    this.cache.delete(intersectionId)
    this.engine.requestRender()
  }

  cacheStats(): { overview: number; medium: number; full: number } {
    return {
      overview: this.overviewCache.size,
      medium: this.mediumCache.size,
      full: this.cache.size,
    }
  }

  visibleCongestionManifests(): RealisticIntersectionManifest[] {
    const visible = new Map<string, RealisticIntersectionManifest>()
    for (const [intersectionId, cached] of this.mediumCache) {
      if (cached.object.group.visible) visible.set(intersectionId, cached.manifest)
    }
    for (const [intersectionId, cached] of this.cache) {
      if (cached.object.group.visible) visible.set(intersectionId, cached.manifest)
    }
    return [...visible.values()]
  }

  activate(intersectionId: string): RealisticIntersectionManifest {
    const cached = this.cache.get(intersectionId)
    if (!cached) throw new Error(`Intersection ${intersectionId} has not been prepared`)
    this.activeId = intersectionId
    cached.usedAt = performance.now()
    if (this.lastViewport) {
      this.refreshViewport(this.lastViewport.center, this.lastViewport.rangeMeters)
    } else {
      for (const [id, overview] of this.overviewCache) overview.object.group.visible = id !== intersectionId
      for (const medium of this.mediumCache.values()) medium.object.group.visible = false
      for (const [id, detail] of this.cache) detail.object.group.visible = id === intersectionId
      this.lodById.set(intersectionId, 'full')
    }
    this.trimCache()
    this.engine.requestRender()
    return cached.manifest
  }

  refreshViewport(
    center: readonly number[] = this.engine.map.getCenter(),
    rangeMeters = this.engine.map.getRange(),
  ): void {
    if (center.length < 2) return
    const viewport: IntersectionViewport = {
      center: [Number(center[0]), Number(center[1])],
      rangeMeters: Number.isFinite(rangeMeters) ? Math.max(0, rangeMeters) : Number.POSITIVE_INFINITY,
    }
    this.lastViewport = viewport
    const candidates = [...this.overviewCache.entries()].map(([intersectionId, overview]) => {
      const projected = this.projector([
        overview.manifest.origin.longitude,
        overview.manifest.origin.latitude,
        0,
      ])
      const distance = geographicDistanceMeters(viewport.center, [projected[0], projected[1]])
      const desired = resolveIntersectionRoadLod({
        cameraRangeMeters: viewport.rangeMeters,
        distanceMeters: distance,
        active: intersectionId === this.activeId,
        previous: this.lodById.get(intersectionId),
      })
      return { intersectionId, distance, desired }
    })
    const fullIds = new Set(candidates
      .filter((candidate) => candidate.desired === 'full')
      .sort((left, right) => {
        if (left.intersectionId === this.activeId) return -1
        if (right.intersectionId === this.activeId) return 1
        return left.distance - right.distance
      })
      .slice(0, this.cacheLimit)
      .map((candidate) => candidate.intersectionId))
    const mediumIds = new Set(candidates
      .filter((candidate) => candidate.desired === 'medium' || (
        candidate.desired === 'full' && !fullIds.has(candidate.intersectionId)
      ))
      .sort((left, right) => left.distance - right.distance)
      .slice(0, this.mediumCacheLimit)
      .map((candidate) => candidate.intersectionId))

    for (const candidate of candidates) {
      const desired = candidate.desired === 'full' && !fullIds.has(candidate.intersectionId)
        ? 'medium'
        : candidate.desired
      this.lodById.set(candidate.intersectionId, desired)
      const overview = this.overviewCache.get(candidate.intersectionId)
      let medium = this.mediumCache.get(candidate.intersectionId)
      if (
        !this.interactionActive
        && desired === 'medium'
        && mediumIds.has(candidate.intersectionId)
        && !medium
        && overview
      ) {
        const object = new RealisticIntersectionObject(overview.manifest, 'medium')
        this.placeAtIntersection(object.group, overview.manifest)
        object.group.visible = false
        this.engine.add(object.group)
        medium = { manifest: overview.manifest, object, usedAt: performance.now() }
        this.mediumCache.set(candidate.intersectionId, medium)
      }
      const detail = this.cache.get(candidate.intersectionId)
      const actual = desired === 'full' && !detail
        ? medium ? 'medium' : 'overview'
        : desired === 'medium' && !medium
          ? 'overview'
          : desired
      if (overview) overview.object.group.visible = actual === 'overview'
      if (medium) {
        medium.object.group.visible = actual === 'medium'
        if (actual === 'medium') medium.usedAt = performance.now()
        medium.object.setArrowsVisible(viewport.rangeMeters <= LANE_ARROW_MAX_VISIBLE_RANGE_METERS)
      }
      if (detail) {
        detail.object.group.visible = actual === 'full'
        detail.object.setArrowsVisible(viewport.rangeMeters <= LANE_ARROW_MAX_VISIBLE_RANGE_METERS)
        if (actual === 'full') detail.usedAt = performance.now()
      }
      if (!this.interactionActive && desired === 'full' && !detail) {
        void this.prepare(candidate.intersectionId).catch((cause: unknown) => {
          console.warn(`[intersection-lod] failed to prepare ${candidate.intersectionId}`, cause)
        })
      }
    }
    if (!this.interactionActive) {
      this.trimCache()
      this.trimMediumCache(mediumIds)
    }
    this.engine.requestRender()
  }

  async switchTo(intersectionId: string): Promise<RealisticIntersectionManifest> {
    this.pendingActivations.add(intersectionId)
    try {
      await this.prepare(intersectionId)
      return this.activate(intersectionId)
    } finally {
      this.pendingActivations.delete(intersectionId)
      this.trimCache()
    }
  }

  updateSignals(intersections: RealisticSignalRuntimeState[] | null): void {
    for (const [intersectionId, cached] of this.cache) {
      const runtime = intersections?.find((item) => item.intersection_id === intersectionId) ?? null
      cached.object.updateSignalState(runtime)
    }
    this.engine.requestRender()
  }

  updateRuntimeDisturbances(events: readonly RealisticRuntimeDisturbance[]): void {
    const grouped = new Map<string, RealisticRuntimeDisturbance[]>()
    for (const event of events) {
      grouped.set(event.intersectionId, [
        ...(grouped.get(event.intersectionId) ?? []),
        { ...event, laneIds: [...event.laneIds] },
      ])
    }
    const intersectionIds = new Set([
      ...this.runtimeDisturbances.keys(),
      ...grouped.keys(),
    ])
    let changed = false
    for (const intersectionId of intersectionIds) {
      const next = grouped.get(intersectionId) ?? []
      const signature = JSON.stringify(next.map((event) => ({
        eventId: event.eventId,
        eventType: event.eventType,
        state: event.state,
        laneIds: [...event.laneIds].sort(),
        positionRatio: event.positionRatio,
      })))
      if (this.runtimeDisturbanceSignatures.get(intersectionId) === signature) continue
      changed = true
      this.runtimeDisturbanceSignatures.set(intersectionId, signature)
      if (next.length > 0) this.runtimeDisturbances.set(intersectionId, next)
      else this.runtimeDisturbances.delete(intersectionId)
      this.cache.get(intersectionId)?.object.updateRuntimeDisturbances(next)
    }
    if (changed) this.engine.requestRender()
  }

  destroy(): void {
    for (const cached of this.cache.values()) {
      this.engine.remove(cached.object.group)
      cached.object.dispose()
    }
    for (const cached of this.overviewCache.values()) {
      this.engine.remove(cached.object.group)
      cached.object.dispose()
    }
    for (const cached of this.mediumCache.values()) {
      this.engine.remove(cached.object.group)
      cached.object.dispose()
    }
    this.cache.clear()
    this.mediumCache.clear()
    this.overviewCache.clear()
    this.manifests.clear()
    this.manifestRequests.clear()
    this.detailRequests.clear()
    this.detailConsumers.clear()
    this.pendingActivations.clear()
    this.lodById.clear()
    this.runtimeDisturbances.clear()
    this.runtimeDisturbanceSignatures.clear()
    this.lastViewport = null
    this.activeId = null
    this.interactionActive = false
  }

  private async loadManifest(intersectionId: string): Promise<RealisticIntersectionManifest> {
    const cached = this.manifests.get(intersectionId)
    if (cached) return cached
    let request = this.manifestRequests.get(intersectionId)
    if (!request) {
      request = loadIntersectionManifest(realisticIntersectionAssetUrl(intersectionId))
        .then((manifest) => {
          this.manifests.set(intersectionId, manifest)
          this.manifestRequests.delete(intersectionId)
          return manifest
        })
        .catch((cause) => {
          this.manifestRequests.delete(intersectionId)
          throw cause
        })
      this.manifestRequests.set(intersectionId, request)
    }
    return request
  }

  private placeAtIntersection(
    group: THREE.Group,
    manifest: RealisticIntersectionManifest,
  ): void {
    const projected = this.projector([manifest.origin.longitude, manifest.origin.latitude, 0])
    const geographicPosition = projected[2] == null
      ? [projected[0], projected[1]]
      : [projected[0], projected[1], projected[2]]
    const scenePosition = this.engine.map.projectArrayCoordinate(geographicPosition)
    group.position.set(
      scenePosition[0],
      scenePosition[1],
      (scenePosition[2] ?? 0) + REALISTIC_INTERSECTION_SURFACE_Z,
    )
  }

  private trimCache(): void {
    const candidates = [...this.cache.entries()]
      .filter(([id]) => (
        id !== this.activeId
        && !this.pendingActivations.has(id)
        && !this.detailRequests.has(id)
        && this.lodById.get(id) !== 'full'
      ))
      .sort((left, right) => left[1].usedAt - right[1].usedAt)
    while (this.cache.size > this.cacheLimit && candidates.length > 0) {
      const [id, cached] = candidates.shift()!
      this.engine.remove(cached.object.group)
      cached.object.dispose()
      this.cache.delete(id)
    }
  }

  private trimMediumCache(keepIds: Set<string> = new Set()): void {
    const candidates = [...this.mediumCache.entries()]
      .filter(([id]) => id !== this.activeId && !keepIds.has(id))
      .sort((left, right) => left[1].usedAt - right[1].usedAt)
    while (this.mediumCache.size > this.mediumCacheLimit && candidates.length > 0) {
      const [id, cached] = candidates.shift()!
      this.engine.remove(cached.object.group)
      cached.object.dispose()
      this.mediumCache.delete(id)
    }
  }
}
