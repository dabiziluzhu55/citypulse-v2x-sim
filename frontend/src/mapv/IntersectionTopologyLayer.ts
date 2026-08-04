import * as mapvthree from '@baidumap/mapv-three'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { RoadCoordinateProjector } from './roadGeometry'
import {
  buildIntersectionTopologyLinks,
  loadIntersectionTopologyCatalog,
  type IntersectionTopologyNode,
} from './intersectionTopology'
import {
  loadIntersectionTopologyRoutes,
  type IntersectionTopologyRoute,
} from './intersectionTopologyRoutes'
import { formatIntersectionLabel } from '../utils/intersectionLabels'
import { distanceMeters as geographicDistanceMeters } from './vehicleVisibility'
import { topologyFlowHeight } from './topologyFlowElevation'
import { REALISTIC_INTERSECTION_SURFACE_Z } from './sceneElevation'
import {
  INTERSECTION_MARKER_LABEL_HEIGHT_METERS,
  INTERSECTION_MARKER_EFFECT_OPTIONS,
  INTERSECTION_MARKER_MODEL_URL,
  INTERSECTION_MARKER_SURFACE_OFFSET_METERS,
  INTERSECTION_MARKER_WAVE_OPTIONS,
  createFallbackIntersectionMarkerModel,
  partitionIntersectionMarkerFeatures,
  shouldShowIntersectionMarkerLabel,
  type IntersectionMarkerFeature,
} from './intersectionMarkerStyle'

interface RenderMaterialOwner {
  material?: THREE.Material
  renderOrder?: number
}

interface TopologyRouteEntry {
  id: string
  baseFeature: Record<string, unknown>
  flowFeature: Record<string, unknown>
  samples: Array<[number, number]>
}

function cloneMarkerModel(source: THREE.Object3D, active: boolean): THREE.Object3D {
  const clone = source.clone(true)
  clone.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    child.geometry = child.geometry.clone()
    const sourceMaterials = Array.isArray(child.material) ? child.material : [child.material]
    const materials = sourceMaterials.map((sourceMaterial) => {
      const material = sourceMaterial.clone()
      for (const [key, value] of Object.entries(sourceMaterial)) {
        if (value instanceof THREE.Texture) {
          ;(material as unknown as Record<string, unknown>)[key] = value.clone()
        }
      }
      const styled = material as THREE.Material & {
        color?: THREE.Color
        emissive?: THREE.Color
        emissiveIntensity?: number
        metalness?: number
        roughness?: number
      }
      styled.color?.multiply(new THREE.Color(active ? '#9cffff' : '#5bbcff'))
      styled.emissive?.set(active ? '#00efd5' : '#006db5')
      if (styled.emissiveIntensity != null) styled.emissiveIntensity = active ? 2.8 : 1.55
      if (styled.metalness != null) styled.metalness = Math.min(styled.metalness, 0.3)
      if (styled.roughness != null) styled.roughness = Math.min(styled.roughness, 0.42)
      return material
    })
    child.material = Array.isArray(child.material) ? materials : materials[0]
  })
  return clone
}

function disposeMarkerModels(models: THREE.Object3D[]): void {
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  const textures = new Set<THREE.Texture>()
  models.forEach((model) => model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    geometries.add(child.geometry)
    const values = Array.isArray(child.material) ? child.material : [child.material]
    values.forEach((material) => {
      materials.add(material)
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) textures.add(value)
      })
    })
  }))
  geometries.forEach((geometry) => geometry.dispose())
  materials.forEach((material) => material.dispose())
  textures.forEach((texture) => texture.dispose())
}

function configureOverlayMaterial(layer: unknown, opacity: number): void {
  const owner = layer as RenderMaterialOwner
  if (!owner.material) return
  owner.material.transparent = true
  owner.material.opacity = opacity
  owner.material.depthTest = true
  owner.material.depthWrite = false
  owner.material.blending = THREE.AdditiveBlending
  owner.material.side = THREE.DoubleSide
  owner.material.polygonOffset = true
  owner.material.polygonOffsetFactor = -6
  owner.material.polygonOffsetUnits = -6
}

export class IntersectionTopologyLayer {
  private readonly baseLine: mapvthree.Polyline
  private readonly flowLine: mapvthree.Polyline
  private readonly markers: mapvthree.EffectModelPoint
  private readonly activeMarker: mapvthree.EffectModelPoint
  private readonly waves: mapvthree.EffectPoint
  private readonly labels: mapvthree.Text
  private markerModel: THREE.Object3D = createFallbackIntersectionMarkerModel(false)
  private activeMarkerModel: THREE.Object3D = createFallbackIntersectionMarkerModel(true)
  private nodes: IntersectionTopologyNode[] = []
  private markerFeatures: IntersectionMarkerFeature[] = []
  private routeEntries: TopologyRouteEntry[] = []
  private visibleRouteKey = ''
  private visibleRouteCount = 0
  private activeIntersectionId: string | null = null
  private currentRangeMeters = Number.POSITIVE_INFINITY
  private activeLabelKey = ''
  private destroyed = false

  constructor(
    private readonly engine: mapvthree.Engine,
    private readonly projector: RoadCoordinateProjector,
  ) {
    this.baseLine = engine.add(new mapvthree.Polyline({
      flat: true,
      isCurve: false,
      color: new THREE.Color('#087dff'),
      lineWidth: 4,
      keepSize: true,
      transparent: true,
      opacity: 0.24,
      height: 0,
    }))
    this.flowLine = engine.add(new mapvthree.Polyline({
      flat: true,
      isCurve: false,
      color: new THREE.Color('#00d9ff'),
      lineWidth: 2,
      keepSize: true,
      transparent: true,
      opacity: 0.9,
      enableAnimation: true,
      enableAnimationChaos: false,
      animationInterval: 2,
      animationTailType: 1,
      animationTailRatio: 0.16,
      animationSpeed: 0.85,
      animationIdle: 1_600,
      height: 0,
    }))
    configureOverlayMaterial(this.baseLine, 0.24)
    configureOverlayMaterial(this.flowLine, 0.9)
    this.baseLine.renderOrder = 34
    this.flowLine.renderOrder = 35

    this.markers = engine.add(new mapvthree.EffectModelPoint(INTERSECTION_MARKER_EFFECT_OPTIONS))
    this.markers.model = this.markerModel
    this.markers.position.z = 0

    this.activeMarker = engine.add(new mapvthree.EffectModelPoint(INTERSECTION_MARKER_EFFECT_OPTIONS))
    this.activeMarker.model = this.activeMarkerModel
    this.activeMarker.position.z = 0

    this.waves = engine.add(new mapvthree.EffectPoint(INTERSECTION_MARKER_WAVE_OPTIONS))
    this.waves.position.z = 0.02

    this.labels = engine.add(new mapvthree.Text({
      fillStyle: '#bff5ff',
      strokeStyle: '#071626',
      lineWidth: 3,
      fontSize: 12,
      flat: false,
      keepSize: true,
    }))
    this.labels.renderOrder = 45
  }

  get animationActive(): boolean {
    return this.nodes.length > 0 && this.visibleRouteCount > 0
  }

  async load(
    url = '/intersections/v3/catalog.json',
    routeUrl = '/intersections/v3/topology-routes.json',
  ): Promise<IntersectionTopologyNode[]> {
    const [nodes, routeManifest] = await Promise.all([
      loadIntersectionTopologyCatalog(url),
      loadIntersectionTopologyRoutes(routeUrl),
      this.loadMarkerModels(),
    ])
    const links = buildIntersectionTopologyLinks(nodes)
    const routesById = new Map(routeManifest.routes.map((route) => [route.routeId, route]))
    const missingRoutes = links.filter((link) => !routesById.has(link.id))
    if (missingRoutes.length > 0) {
      throw new Error(`Intersection topology routes are missing: ${missingRoutes.map((link) => link.id).join(', ')}`)
    }
    const pointFeatures: IntersectionMarkerFeature[] = nodes.map((node) => {
      const elevation = REALISTIC_INTERSECTION_SURFACE_Z + INTERSECTION_MARKER_SURFACE_OFFSET_METERS
      const coordinates = this.projector([
          node.longitude,
          node.latitude,
          elevation,
      ])
      return {
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [coordinates[0], coordinates[1], coordinates[2] ?? elevation],
        },
        properties: { intersection_id: node.intersectionId },
      }
    })
    this.nodes = nodes
    this.markerFeatures = pointFeatures
    this.routeEntries = links.map((link) => this.routeEntry(routesById.get(link.id)!, link.distanceMeters))
    const baseFeatures = this.routeEntries.map((entry) => entry.baseFeature)
    const flowFeatures = this.routeEntries.map((entry) => entry.flowFeature)
    this.refreshMarkerSources()
    this.baseLine.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: baseFeatures })
    this.flowLine.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: flowFeatures })
    this.visibleRouteKey = this.routeEntries.map((entry) => entry.id).join('|')
    this.visibleRouteCount = this.routeEntries.length
    this.refreshViewport()
    this.engine.requestRender()
    return nodes
  }

  refreshViewport(
    center: readonly number[] = this.engine.map.getCenter(),
    rangeMeters = this.engine.map.getRange(),
  ): void {
    const finiteRange = Number.isFinite(rangeMeters) ? Math.max(0, rangeMeters) : Number.POSITIVE_INFINITY
    this.currentRangeMeters = finiteRange
    this.refreshActiveLabel()
    if (center.length < 2 || this.routeEntries.length === 0) return
    const visibleRadius = Math.max(4_500, finiteRange * 1.6)
    const visible = finiteRange >= 12_000
      ? this.routeEntries
      : this.routeEntries.filter((entry) => entry.samples.some((sample) => (
        geographicDistanceMeters(center, sample) <= visibleRadius
      )))
    const key = visible.map((entry) => entry.id).join('|')
    if (key === this.visibleRouteKey) return
    const baseCollection = {
      type: 'FeatureCollection',
      features: visible.map((entry) => entry.baseFeature),
    }
    const flowCollection = {
      type: 'FeatureCollection',
      features: visible.map((entry) => entry.flowFeature),
    }
    this.baseLine.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(baseCollection)
    this.flowLine.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(flowCollection)
    this.visibleRouteKey = key
    this.visibleRouteCount = visible.length
    this.engine.requestRender()
  }

  setActiveIntersection(intersectionId: string): void {
    this.activeIntersectionId = this.nodes.some((node) => node.intersectionId === intersectionId)
      ? intersectionId
      : null
    this.refreshMarkerSources()
    this.refreshActiveLabel()
    this.engine.requestRender()
  }

  destroy(): void {
    this.destroyed = true
    this.baseLine.dataSource?.clear()
    this.flowLine.dataSource?.clear()
    this.markers.dataSource?.clear()
    this.activeMarker.dataSource?.clear()
    this.waves.dataSource?.clear()
    this.labels.dataSource?.clear()
    this.engine.remove(this.baseLine)
    this.engine.remove(this.flowLine)
    this.engine.remove(this.markers)
    this.engine.remove(this.activeMarker)
    this.engine.remove(this.waves)
    this.engine.remove(this.labels)
    disposeMarkerModels([this.markerModel, this.activeMarkerModel])
    this.nodes = []
    this.markerFeatures = []
    this.routeEntries = []
    this.visibleRouteKey = ''
    this.visibleRouteCount = 0
    this.activeIntersectionId = null
    this.activeLabelKey = ''
  }

  private async loadMarkerModels(): Promise<void> {
    try {
      const gltf = await new GLTFLoader().loadAsync(INTERSECTION_MARKER_MODEL_URL)
      const normal = cloneMarkerModel(gltf.scene, false)
      const active = cloneMarkerModel(gltf.scene, true)
      disposeMarkerModels([gltf.scene])
      if (this.destroyed) {
        disposeMarkerModels([normal, active])
        return
      }
      const previous = [this.markerModel, this.activeMarkerModel]
      this.markerModel = normal
      this.activeMarkerModel = active
      this.markers.model = normal
      this.activeMarker.model = active
      disposeMarkerModels(previous)
    } catch (cause) {
      console.warn('[intersection-marker] using local teardrop fallback', cause)
    }
  }

  private refreshMarkerSources(): void {
    const partition = partitionIntersectionMarkerFeatures(
      this.markerFeatures,
      this.activeIntersectionId,
    )
    this.markers.dataSource?.clear()
    this.activeMarker.dataSource?.clear()
    this.waves.dataSource?.clear()
    this.markers.dataSource = partition.normal.length
      ? mapvthree.GeoJSONDataSource.fromGeoJSON({
        type: 'FeatureCollection',
        features: partition.normal,
      })
      : null
    const activeSource = partition.active.length
      ? mapvthree.GeoJSONDataSource.fromGeoJSON({
        type: 'FeatureCollection',
        features: partition.active,
      })
      : null
    this.activeMarker.dataSource = activeSource
    this.waves.dataSource = partition.active.length
      ? mapvthree.GeoJSONDataSource.fromGeoJSON({
        type: 'FeatureCollection',
        features: partition.active,
      })
      : null
  }

  private refreshActiveLabel(): void {
    const node = this.nodes.find((candidate) => (
      candidate.intersectionId === this.activeIntersectionId
    ))
    const visible = shouldShowIntersectionMarkerLabel(this.currentRangeMeters, Boolean(node))
    const key = visible && node ? node.intersectionId : ''
    if (key === this.activeLabelKey) return
    this.activeLabelKey = key
    this.labels.dataSource?.clear()
    if (!visible || !node) {
      this.labels.dataSource = null
      return
    }
    const labelSource = mapvthree.GeoJSONDataSource.fromGeoJSON({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: this.projector([
            node.longitude,
            node.latitude,
            REALISTIC_INTERSECTION_SURFACE_Z + INTERSECTION_MARKER_LABEL_HEIGHT_METERS,
          ]),
        },
        properties: { label: formatIntersectionLabel(node.intersectionId) },
      }],
    })
    labelSource.defineAttribute('text', 'label')
    this.labels.dataSource = labelSource
  }

  private routeEntry(route: IntersectionTopologyRoute, directDistanceMeters: number): TopologyRouteEntry {
    const baseProjected = route.coordinates.map(([longitude, latitude]) => this.projector([
      longitude,
      latitude,
      topologyFlowHeight([longitude, latitude], this.nodes, 'base'),
    ]))
    const flowProjected = route.coordinates.map(([longitude, latitude]) => this.projector([
      longitude,
      latitude,
      topologyFlowHeight([longitude, latitude], this.nodes, 'flow'),
    ]))
    const properties = {
      topology_id: route.routeId,
      distance_m: Math.round(directDistanceMeters),
      route_length_m: Math.round(route.lengthMeters),
    }
    return {
      id: route.routeId,
      baseFeature: {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: baseProjected,
        },
        properties,
      },
      flowFeature: {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: flowProjected,
        },
        properties,
      },
      samples: baseProjected
        .filter((_, index) => index % 8 === 0 || index === baseProjected.length - 1)
        .map((coordinate) => [coordinate[0], coordinate[1]]),
    }
  }
}
