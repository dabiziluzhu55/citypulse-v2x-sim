import * as mapvthree from '@baidumap/mapv-three'
import * as THREE from 'three'
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

function createMarkerModel(color: number, emissive: number): THREE.Group {
  const group = new THREE.Group()
  const material = new THREE.MeshStandardMaterial({
    color,
    emissive,
    emissiveIntensity: 2.4,
    metalness: 0.18,
    roughness: 0.3,
  })
  const crown = new THREE.Mesh(new THREE.OctahedronGeometry(0.72, 0), material)
  crown.position.y = 0.35
  crown.scale.set(0.86, 1.18, 0.86)
  const pointer = new THREE.Mesh(new THREE.ConeGeometry(0.34, 1.25, 5), material)
  pointer.position.y = -0.7
  group.add(crown, pointer)
  return group
}

function disposeMarkerModel(model: THREE.Object3D): void {
  const materials = new Set<THREE.Material>()
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    child.geometry.dispose()
    const values = Array.isArray(child.material) ? child.material : [child.material]
    values.forEach((material) => materials.add(material))
  })
  materials.forEach((material) => material.dispose())
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
  private readonly markerModel = createMarkerModel(0x087ae8, 0x00aef0)
  private readonly activeMarkerModel = createMarkerModel(0x08a7f0, 0x00f5dc)
  private nodes: IntersectionTopologyNode[] = []
  private routeEntries: TopologyRouteEntry[] = []
  private visibleRouteKey = ''
  private visibleRouteCount = 0

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

    this.markers = engine.add(new mapvthree.EffectModelPoint({
      normalize: true,
      rotateToZUp: true,
      keepSize: true,
      size: 42,
      height: 18,
      animationJump: true,
      animationRotate: true,
      animationRotatePeriod: 7_500,
    }))
    this.markers.model = this.markerModel
    this.markers.position.z = 8

    this.activeMarker = engine.add(new mapvthree.EffectModelPoint({
      normalize: true,
      rotateToZUp: true,
      keepSize: true,
      size: 54,
      height: 22,
      animationJump: true,
      animationRotate: true,
      animationRotatePeriod: 4_800,
    }))
    this.activeMarker.model = this.activeMarkerModel
    this.activeMarker.position.z = 10

    this.waves = engine.add(new mapvthree.EffectPoint({
      color: '#00c9ff',
      opacity: 0.62,
      keepSize: true,
      size: 46,
      type: 'Wave',
      duration: 2_000,
    }))
    this.waves.position.z = 2

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
    ])
    const links = buildIntersectionTopologyLinks(nodes)
    const routesById = new Map(routeManifest.routes.map((route) => [route.routeId, route]))
    const missingRoutes = links.filter((link) => !routesById.has(link.id))
    if (missingRoutes.length > 0) {
      throw new Error(`Intersection topology routes are missing: ${missingRoutes.map((link) => link.id).join(', ')}`)
    }
    const pointFeatures = nodes.map((node) => ({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: this.projector([node.longitude, node.latitude, 0]),
      },
      properties: { intersection_id: node.intersectionId },
    }))
    this.nodes = nodes
    this.routeEntries = links.map((link) => this.routeEntry(routesById.get(link.id)!, link.distanceMeters))
    const baseFeatures = this.routeEntries.map((entry) => entry.baseFeature)
    const flowFeatures = this.routeEntries.map((entry) => entry.flowFeature)
    const points = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: pointFeatures })
    this.markers.dataSource = points
    this.waves.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: pointFeatures })
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
    if (center.length < 2 || this.routeEntries.length === 0) return
    const finiteRange = Number.isFinite(rangeMeters) ? Math.max(0, rangeMeters) : Number.POSITIVE_INFINITY
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
    const node = this.nodes.find((candidate) => candidate.intersectionId === intersectionId)
    this.activeMarker.dataSource = node
      ? mapvthree.GeoJSONDataSource.fromGeoJSON({
        type: 'FeatureCollection',
        features: [{
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: this.projector([node.longitude, node.latitude, 0]),
          },
          properties: { intersection_id: node.intersectionId },
        }],
      })
      : null
    if (node) {
      const labelSource = mapvthree.GeoJSONDataSource.fromGeoJSON({
        type: 'FeatureCollection',
        features: [{
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: this.projector([node.longitude, node.latitude, 32]),
          },
          properties: { label: formatIntersectionLabel(node.intersectionId) },
        }],
      })
      labelSource.defineAttribute('text', 'label')
      this.labels.dataSource = labelSource
    } else {
      this.labels.dataSource = null
    }
    this.engine.requestRender()
  }

  destroy(): void {
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
    disposeMarkerModel(this.markerModel)
    disposeMarkerModel(this.activeMarkerModel)
    this.nodes = []
    this.routeEntries = []
    this.visibleRouteKey = ''
    this.visibleRouteCount = 0
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
