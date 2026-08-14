import * as mapvthree from '@baidumap/mapv-three'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js'

import type { DetectedEventCard } from '../types/intelligence'
import type { DisturbanceRuntimeView } from '../utils/runtimeDisturbances'
import {
  mergeSceneEventMarkers,
} from './sceneEventMarkerRules.ts'

export { detectedMarkerColor, mergeSceneEventMarkers } from './sceneEventMarkerRules.ts'

export type SceneEventMarkerColor = 'yellow' | 'red'
export type EventMarkerPositionSource =
  | 'detected_coordinates'
  | 'accident_lane'
  | 'venue_lane'
  | 'intersection_fallback'

export interface EventMarkerPosition {
  scene: [number, number, number]
  mapCoordinate: [number, number, number]
  longitude?: number
  latitude?: number
  laneId?: string
  positionRatio?: number
  intersectionId?: string
  source: EventMarkerPositionSource
  fallbackReason?: string
}

export interface DetectedSceneEventDetail {
  kind: 'detected'
  id: string
  card: DetectedEventCard
}

export interface RuntimeSceneEventDetail {
  kind: 'runtime'
  id: string
  event: DisturbanceRuntimeView
}

export type SceneEventDetail = DetectedSceneEventDetail | RuntimeSceneEventDetail

export interface SceneEventMarker {
  id: string
  color: SceneEventMarkerColor
  intersectionId: string
  position: EventMarkerPosition
  details: SceneEventDetail[]
}

export interface SceneEventMarkerStats {
  markerCount: number
  fallbackPositionCount: number
  mergedEventCount: number
  modelLoadMilliseconds: number
  modelBytes: number
  estimatedGpuBytes: number
  modelLoadFailureCount: number
}

function estimateModelGpuBytes(model: THREE.Object3D): number {
  const arrays = new Set<ArrayBufferLike>()
  const textures = new Set<THREE.Texture>()
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    for (const attribute of Object.values(child.geometry.attributes) as THREE.BufferAttribute[]) {
      arrays.add(attribute.array.buffer)
    }
    if (child.geometry.index) arrays.add(child.geometry.index.array.buffer)
    const materials = Array.isArray(child.material) ? child.material : [child.material]
    materials.forEach((material) => Object.values(material).forEach((value) => {
      if (value instanceof THREE.Texture) textures.add(value)
    }))
  })
  const geometryBytes = [...arrays].reduce((total, buffer) => total + buffer.byteLength, 0)
  const textureBytes = [...textures].reduce((total, texture) => {
    const image = texture.image as { width?: number; height?: number } | undefined
    return total + Math.max(0, Number(image?.width) || 0) * Math.max(0, Number(image?.height) || 0) * 4
  }, 0)
  return geometryBytes + textureBytes
}

export const EVENT_MARKER_SIZE_PIXELS = 42
export const EVENT_MARKER_HIT_SIZE_PIXELS = 48
export const EVENT_MARKER_RENDER_ORDER = 1_100
export const EVENT_MARKER_MODEL_URLS: Record<SceneEventMarkerColor, string> = {
  red: '/models/events/event-marker-red.glb',
  yellow: '/models/events/event-marker-yellow.glb',
}

const MARKER_OPTIONS = Object.freeze({
  normalize: false,
  rotateToZUp: false,
  keepSize: true,
  size: EVENT_MARKER_SIZE_PIXELS,
  animationRotate: false,
  animationJump: false,
})

function configureMarkerMaterial(model: THREE.Object3D): THREE.Object3D {
  model.renderOrder = EVENT_MARKER_RENDER_ORDER
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    child.renderOrder = EVENT_MARKER_RENDER_ORDER
    child.frustumCulled = false
    const sourceMaterials = Array.isArray(child.material) ? child.material : [child.material]
    const materials = sourceMaterials.map((material) => {
      material.transparent = true
      material.depthTest = false
      material.depthWrite = false
      material.needsUpdate = true
      return material
    })
    child.material = Array.isArray(child.material) ? materials : materials[0]
  })
  return model
}

export function anchorEventMarkerModel<T extends THREE.Object3D>(model: T): T {
  if (model.userData.eventMarkerBottomAnchored === true) return model
  model.rotation.x += Math.PI / 2
  model.updateMatrixWorld(true)
  const bounds = new THREE.Box3().setFromObject(model)
  const size = bounds.getSize(new THREE.Vector3())
  const maximumDimension = Math.max(size.x, size.y, size.z)
  if (Number.isFinite(maximumDimension) && maximumDimension > 1e-9) {
    model.scale.multiplyScalar(1 / maximumDimension)
  }
  model.updateMatrixWorld(true)
  const normalizedBounds = new THREE.Box3().setFromObject(model)
  const center = normalizedBounds.getCenter(new THREE.Vector3())
  model.position.x -= center.x
  model.position.y -= center.y
  model.position.z -= normalizedBounds.min.z
  model.updateMatrixWorld(true)
  model.userData.eventMarkerBottomAnchored = true
  return configureMarkerMaterial(model) as T
}

export function createFallbackEventMarkerModel(color: SceneEventMarkerColor): THREE.Object3D {
  const group = new THREE.Group()
  const material = new THREE.MeshBasicMaterial({
    color: color === 'red' ? 0xff263f : 0xffd43b,
    transparent: true,
    opacity: 0.96,
    depthTest: false,
    depthWrite: false,
  })
  const profile = [
    new THREE.Vector2(0, -1.25),
    new THREE.Vector2(0.3, -0.58),
    new THREE.Vector2(0.48, 0),
    new THREE.Vector2(0.36, 0.54),
    new THREE.Vector2(0, 0.78),
  ]
  group.add(new THREE.Mesh(new THREE.LatheGeometry(profile, 20), material))
  group.name = `event-marker-${color}-fallback`
  return anchorEventMarkerModel(group)
}

function disposeModel(model: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  const textures = new Set<THREE.Texture>()
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    geometries.add(child.geometry)
    const values = Array.isArray(child.material) ? child.material : [child.material]
    values.forEach((material) => {
      materials.add(material)
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) textures.add(value)
      })
    })
  })
  geometries.forEach((geometry) => geometry.dispose())
  materials.forEach((material) => material.dispose())
  textures.forEach((texture) => texture.dispose())
}

function markerFeature(marker: SceneEventMarker): Record<string, unknown> {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: marker.position.mapCoordinate },
    properties: { marker_id: marker.id },
  }
}

export class SceneEventMarkerLayer {
  private readonly layers: Record<SceneEventMarkerColor, mapvthree.EffectModelPoint>
  private readonly models: Record<SceneEventMarkerColor, THREE.Object3D>
  private readonly interactionAnchors = new Map<string, mapvthree.DOMOverlay>()
  private destroyed = false
  private loadStartedAt = performance.now()
  private modelLoadMilliseconds = 0
  private modelBytes = 0
  private modelLoadFailureCount = 0
  private markers: SceneEventMarker[] = []
  private layoutKey = ''

  constructor(private readonly engine: mapvthree.Engine) {
    this.models = {
      red: createFallbackEventMarkerModel('red'),
      yellow: createFallbackEventMarkerModel('yellow'),
    }
    this.layers = {
      red: engine.add(new mapvthree.EffectModelPoint(MARKER_OPTIONS)),
      yellow: engine.add(new mapvthree.EffectModelPoint(MARKER_OPTIONS)),
    }
    for (const color of ['red', 'yellow'] as const) {
      this.layers[color].model = this.models[color]
      this.layers[color].position.z = 0
      this.layers[color].renderOrder = EVENT_MARKER_RENDER_ORDER
    }
    void this.loadModels()
  }

  get animationActive(): boolean {
    return false
  }

  setMarkers(markers: SceneEventMarker[]): void {
    this.markers = mergeSceneEventMarkers(markers)
    this.syncInteractionAnchors()
    const layoutKey = this.markers.map((marker) => [
      marker.id,
      marker.color,
      ...marker.position.mapCoordinate.map((value) => value.toFixed(6)),
    ].join(':')).sort().join('|')
    if (layoutKey === this.layoutKey) return
    this.layoutKey = layoutKey
    for (const color of ['red', 'yellow'] as const) {
      const features = this.markers.filter((marker) => marker.color === color).map(markerFeature)
      this.layers[color].dataSource?.clear()
      this.layers[color].dataSource = features.length
        ? mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features })
        : null
    }
    this.engine.requestRender()
  }

  projectMarkerToContainer(
    markerId: string,
    container: HTMLElement,
  ): { x: number; y: number } | null {
    const anchor = this.interactionAnchors.get(markerId)
    if (!anchor?.dom || anchor.dom.style.visibility === 'hidden') return null
    const anchorBounds = anchor.dom.getBoundingClientRect()
    if (anchorBounds.width <= 0 || anchorBounds.height <= 0) return null
    const containerBounds = container.getBoundingClientRect()
    const x = anchorBounds.left + anchorBounds.width / 2 - containerBounds.left
    const y = anchorBounds.top + anchorBounds.height / 2 - containerBounds.top
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null
    if (x < 0 || x > containerBounds.width || y < 0 || y > containerBounds.height) return null
    return { x, y }
  }

  stats(): SceneEventMarkerStats {
    return {
      markerCount: this.markers.length,
      fallbackPositionCount: this.markers.filter((marker) => marker.position.source === 'intersection_fallback').length,
      mergedEventCount: this.markers.reduce((sum, marker) => sum + Math.max(0, marker.details.length - 1), 0),
      modelLoadMilliseconds: this.modelLoadMilliseconds,
      modelBytes: this.modelBytes,
      estimatedGpuBytes: estimateModelGpuBytes(this.models.red) + estimateModelGpuBytes(this.models.yellow),
      modelLoadFailureCount: this.modelLoadFailureCount,
    }
  }

  destroy(): void {
    this.destroyed = true
    this.clearInteractionAnchors()
    for (const color of ['red', 'yellow'] as const) {
      this.layers[color].dataSource?.clear()
      this.engine.remove(this.layers[color])
      disposeModel(this.models[color])
    }
    this.markers = []
    this.layoutKey = ''
  }

  private syncInteractionAnchors(): void {
    const markerIds = new Set(this.markers.map((marker) => marker.id))
    for (const [markerId, anchor] of this.interactionAnchors) {
      if (markerIds.has(markerId)) continue
      this.engine.remove(anchor)
      this.interactionAnchors.delete(markerId)
    }
    for (const marker of this.markers) {
      const existing = this.interactionAnchors.get(marker.id)
      if (existing) {
        existing.point = [...marker.position.mapCoordinate]
        continue
      }
      const element = document.createElement('span')
      element.className = 'scene-event-map-anchor'
      element.setAttribute('aria-hidden', 'true')
      Object.assign(element.style, {
        display: 'block',
        width: '1px',
        height: '1px',
        opacity: '0',
        pointerEvents: 'none',
      })
      const anchor = this.engine.add(new mapvthree.DOMOverlay({
        dom: element,
        point: [...marker.position.mapCoordinate],
        offset: [0, 0],
        visible: true,
      }))
      this.interactionAnchors.set(marker.id, anchor)
    }
  }

  private clearInteractionAnchors(): void {
    for (const anchor of this.interactionAnchors.values()) this.engine.remove(anchor)
    this.interactionAnchors.clear()
  }

  private async loadModels(): Promise<void> {
    this.loadStartedAt = performance.now()
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
    const loaded = await Promise.all((['red', 'yellow'] as const).map(async (color) => {
      const started = performance.now()
      try {
        const [gltf, response] = await Promise.all([
          loader.loadAsync(EVENT_MARKER_MODEL_URLS[color]),
          fetch(EVENT_MARKER_MODEL_URLS[color], { method: 'HEAD' }),
        ])
        const bytes = Number(response.headers.get('content-length')) || 0
        return { color, model: anchorEventMarkerModel(gltf.scene), bytes, milliseconds: performance.now() - started }
      } catch (cause) {
        this.modelLoadFailureCount += 1
        console.warn(`[event-marker] ${color} model unavailable; using fallback`, cause)
        return null
      }
    }))
    if (this.destroyed) {
      loaded.forEach((item) => item && disposeModel(item.model))
      return
    }
    for (const item of loaded) {
      if (!item) continue
      const previous = this.models[item.color]
      this.models[item.color] = item.model
      this.layers[item.color].model = item.model
      disposeModel(previous)
      this.modelBytes += item.bytes
    }
    this.modelLoadMilliseconds = performance.now() - this.loadStartedAt
    this.engine.requestRender()
  }
}
