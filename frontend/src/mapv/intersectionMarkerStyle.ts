import * as THREE from 'three'

export const INTERSECTION_MARKER_MODEL_URL = '/models/yizhuang_cross_1.glb'
export const INTERSECTION_MARKER_SIZE_METERS = 30
export const INTERSECTION_MARKER_ROTATION_PERIOD_MS = 8_000
export const INTERSECTION_MARKER_SURFACE_OFFSET_METERS = 0.1
export const INTERSECTION_MARKER_LABEL_HEIGHT_METERS = 32
export const INTERSECTION_MARKER_LABEL_MAX_RANGE_METERS = 3_000

export const INTERSECTION_MARKER_EFFECT_OPTIONS = Object.freeze({
  normalize: true,
  rotateToZUp: true,
  keepSize: false,
  size: INTERSECTION_MARKER_SIZE_METERS,
  height: 0,
  animationJump: false,
  animationRotate: true,
  animationRotatePeriod: INTERSECTION_MARKER_ROTATION_PERIOD_MS,
})

export const INTERSECTION_MARKER_WAVE_OPTIONS = Object.freeze({
  color: '#00c9ff',
  opacity: 0.54,
  keepSize: false,
  size: 38,
  type: 'Wave',
  duration: 2_000,
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

export function createFallbackIntersectionMarkerModel(active: boolean): THREE.Group {
  const group = new THREE.Group()
  group.name = active ? 'active-teardrop-marker-fallback' : 'teardrop-marker-fallback'
  const material = new THREE.MeshStandardMaterial({
    color: active ? 0x16d8ff : 0x0789e8,
    emissive: active ? 0x00f5dc : 0x00aef0,
    emissiveIntensity: active ? 3.2 : 2.2,
    metalness: 0.12,
    roughness: 0.28,
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
  return group
}
