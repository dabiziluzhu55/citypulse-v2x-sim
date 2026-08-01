import * as mapvthree from '@baidumap/mapv-three'
import * as THREE from 'three'
import type { RoadCoordinateProjector } from './roadGeometry'
import {
  buildIntersectionTopologyLinks,
  loadIntersectionTopologyCatalog,
  type IntersectionTopologyNode,
} from './intersectionTopology'
import { formatIntersectionLabel } from '../utils/intersectionLabels'

interface RenderMaterialOwner {
  material?: THREE.Material
  renderOrder?: number
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
  owner.material.depthTest = false
  owner.material.depthWrite = false
  owner.material.blending = THREE.AdditiveBlending
  owner.material.side = THREE.DoubleSide
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

  constructor(
    private readonly engine: mapvthree.Engine,
    private readonly projector: RoadCoordinateProjector,
  ) {
    this.baseLine = engine.add(new mapvthree.Polyline({
      flat: true,
      isCurve: true,
      color: new THREE.Color(0.02, 0.42, 1.8),
      lineWidth: 2,
      keepSize: true,
      transparent: true,
      opacity: 0.28,
      height: 5,
    }))
    this.flowLine = engine.add(new mapvthree.Polyline({
      flat: true,
      isCurve: true,
      color: new THREE.Color(0.08, 1.5, 4.8),
      lineWidth: 3,
      keepSize: true,
      transparent: true,
      opacity: 0.72,
      enableAnimation: true,
      enableAnimationChaos: true,
      animationInterval: 2,
      animationTailType: 1,
      animationTailRatio: 0.16,
      animationSpeed: 1,
      animationIdle: 1_600,
      height: 7,
    }))
    configureOverlayMaterial(this.baseLine, 0.28)
    configureOverlayMaterial(this.flowLine, 0.72)

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
    return this.nodes.length > 0
  }

  async load(url = '/intersections/v3/catalog.json'): Promise<IntersectionTopologyNode[]> {
    const nodes = await loadIntersectionTopologyCatalog(url)
    const links = buildIntersectionTopologyLinks(nodes)
    const pointFeatures = nodes.map((node) => ({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: this.projector([node.longitude, node.latitude, 0]),
      },
      properties: { intersection_id: node.intersectionId },
    }))
    const lineFeatures = links.map((link) => ({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          this.projector([link.from.longitude, link.from.latitude, 7]),
          this.projector([link.to.longitude, link.to.latitude, 7]),
        ],
      },
      properties: {
        topology_id: link.id,
        distance_m: Math.round(link.distanceMeters),
      },
    }))
    const points = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: pointFeatures })
    const lines = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: lineFeatures })
    this.markers.dataSource = points
    this.waves.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: pointFeatures })
    this.baseLine.dataSource = lines
    this.flowLine.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: lineFeatures })
    this.nodes = nodes
    this.engine.requestRender()
    return nodes
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
  }
}
