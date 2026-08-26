import * as THREE from 'three'

export const INTERSECTION_MARKER_MODEL_URL = '/models/yizhuang_cross_1.glb'
export const INTERSECTION_MARKER_SIZE_METERS = 30
export const ACTIVE_INTERSECTION_MARKER_SIZE_PIXELS = 54
export const ACTIVE_INTERSECTION_MARKER_WAVE_SIZE_PIXELS = 68
export const INTERSECTION_MARKER_ROTATION_PERIOD_MS = 8_000
export const INTERSECTION_MARKER_SURFACE_OFFSET_METERS = 0.1
export const INTERSECTION_MARKER_LABEL_HEIGHT_METERS = 32
export const INTERSECTION_MARKER_LABEL_MAX_RANGE_METERS = 3_000

export const INTERSECTION_MARKER_EFFECT_OPTIONS = Object.freeze({
  normalize: false,
  rotateToZUp: false,
  keepSize: false,
  size: INTERSECTION_MARKER_SIZE_METERS,
  height: 0,
  animationJump: false,
  animationRotate: true,
  animationRotatePeriod: INTERSECTION_MARKER_ROTATION_PERIOD_MS,
})

export const ACTIVE_INTERSECTION_MARKER_EFFECT_OPTIONS = Object.freeze({
  ...INTERSECTION_MARKER_EFFECT_OPTIONS,
  keepSize: true,
  size: ACTIVE_INTERSECTION_MARKER_SIZE_PIXELS,
})

export const INTERSECTION_MARKER_WAVE_OPTIONS = Object.freeze({
  color: '#00c9ff',
  opacity: 0.54,
  keepSize: false,
  size: 38,
  type: 'Wave',
  duration: 2_000,
})

export const ACTIVE_INTERSECTION_MARKER_WAVE_OPTIONS = Object.freeze({
  ...INTERSECTION_MARKER_WAVE_OPTIONS,
  keepSize: true,
  size: ACTIVE_INTERSECTION_MARKER_WAVE_SIZE_PIXELS,
})

export interface IntersectionMarkerFeature {
  type: 'Feature'
  geometry: {
    type: 'Point'
    coordinates: number[]
  }
  properties: {
    intersection_id: string
  }
}

export function partitionIntersectionMarkerFeatures(
  features: IntersectionMarkerFeature[],
  activeIntersectionId: string | null,
): { normal: IntersectionMarkerFeature[]; active: IntersectionMarkerFeature[] } {
  if (!activeIntersectionId) return { normal: [...features], active: [] }
  return {
    normal: features.filter((feature) => (
      feature.properties.intersection_id !== activeIntersectionId
    )),
    active: features.filter((feature) => (
      feature.properties.intersection_id === activeIntersectionId
    )),
  }
}

export function shouldShowIntersectionMarkerLabel(
  rangeMeters: number,
  hasActiveIntersection: boolean,
): boolean {
  return hasActiveIntersection
    && Number.isFinite(rangeMeters)
    && rangeMeters <= INTERSECTION_MARKER_LABEL_MAX_RANGE_METERS
}

export function markerWorldProjectionRatio(
  rangeMeters: number,
  sizeMeters = INTERSECTION_MARKER_SIZE_METERS,
): number {
  return sizeMeters / Math.max(1, rangeMeters)
}

export function anchorIntersectionMarkerModel<T extends THREE.Object3D>(model: T): T {
  if (model.userData.intersectionMarkerBottomAnchored === true) return model
  model.rotation.x += Math.PI / 2
  model.updateMatrixWorld(true)
  const initialBounds = new THREE.Box3().setFromObject(model)
  const size = initialBounds.getSize(new THREE.Vector3())
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
  model.userData.intersectionMarkerBottomAnchored = true
  return model
}

export const ACTIVE_INTERSECTION_MARKER_RENDER_ORDER = 1_000
export const ACTIVE_INTERSECTION_MARKER_WAVE_RENDER_ORDER = 999

export function configureSelectedIntersectionMarkerModel<T extends THREE.Object3D>(model: T): T {
  model.renderOrder = ACTIVE_INTERSECTION_MARKER_RENDER_ORDER
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    child.renderOrder = ACTIVE_INTERSECTION_MARKER_RENDER_ORDER
    child.frustumCulled = false
    const materials = Array.isArray(child.material) ? child.material : [child.material]
    for (const material of materials) {
      material.transparent = true
      material.depthTest = false
      material.depthWrite = false
      material.needsUpdate = true
    }
  })
  return model
}

export function createFallbackIntersectionMarkerModel(active: boolean): THREE.Group {
  const group = new THREE.Group()
  group.name = active ? 'active-teardrop-marker-fallback' : 'teardrop-marker-fallback'
  const material = new THREE.MeshStandardMaterial({
    color: active ? 0x16d8ff : 0x0789e8,
    emissive: active ? 0x00f5dc : 0x00aef0,
    emissiveIntensity: active ? 3.2 : 2.2,
    metalness: 0.12,
    roughness: 0.28,
    depthWrite: !active,
  })
  const profile = [
    new THREE.Vector2(0, -1.42),
    new THREE.Vector2(0.28, -0.74),
    new THREE.Vector2(0.52, -0.08),
    new THREE.Vector2(0.43, 0.48),
    new THREE.Vector2(0.18, 0.78),
    new THREE.Vector2(0, 0.86),
  ]
  group.add(new THREE.Mesh(new THREE.LatheGeometry(profile, 24), material))
  const anchored = anchorIntersectionMarkerModel(group)
  return active ? configureSelectedIntersectionMarkerModel(anchored) : anchored
}
