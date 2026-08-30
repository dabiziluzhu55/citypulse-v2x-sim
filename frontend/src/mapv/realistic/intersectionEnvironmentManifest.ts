import type { ShowcaseGeoJsonLayerUrls } from '../showcaseLayers/ShowcaseGeoJsonLayers.ts'
import type { ShowcaseLandmark } from '../showcaseLayers/ShowcaseModelLayers.ts'
import { fetchJsonAsset } from '../../utils/fetchJsonAsset.ts'

export interface IntersectionEnvironmentManifest {
  schemaVersion: 1
  intersectionId: string
  facilitiesUrl?: string
  buildingTilesetUrl?: string
  streetlight?: {
    modelUrl: string
    heightMeters: number
    modelYawDegrees?: number
  }
  vegetation?: {
    manifestUrl: string
    modelUrl: string
  }
  geojson?: ShowcaseGeoJsonLayerUrls
  detailModel?: ShowcaseLandmark
}

function parseStreetlight(value: unknown): IntersectionEnvironmentManifest['streetlight'] {
  if (value == null) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Streetlight configuration must be an object')
  }
  const source = value as Record<string, unknown>
  const modelUrl = optionalString(source.modelUrl)
  const heightMeters = Number(source.heightMeters)
  const modelYawDegrees = source.modelYawDegrees == null
    ? undefined
    : Number(source.modelYawDegrees)
  if (
    !modelUrl
    || !Number.isFinite(heightMeters)
    || heightMeters <= 0
    || (source.modelYawDegrees != null && !Number.isFinite(modelYawDegrees))
  ) {
    throw new Error('Streetlight configuration is incomplete')
  }
  return {
    modelUrl,
    heightMeters,
    ...(modelYawDegrees == null ? {} : { modelYawDegrees }),
  }
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function numericTuple(value: unknown, length: number): number[] | undefined {
  if (!Array.isArray(value) || value.length !== length || !value.every(Number.isFinite)) return undefined
  return value as number[]
}

function parseDetailModel(value: unknown): ShowcaseLandmark | undefined {
  if (value == null) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Intersection detail model must be an object')
  }
  const source = value as Record<string, unknown>
  const url = optionalString(source.url)
  const position = numericTuple(source.position, 3)
  const rotation = source.rotation == null ? undefined : numericTuple(source.rotation, 3)
  const scale = source.scale == null ? undefined : Number(source.scale)
  if (!url || !position || (source.rotation != null && !rotation) || (scale != null && (!Number.isFinite(scale) || scale <= 0))) {
    throw new Error('Intersection detail model is incomplete')
  }
  return {
    url,
    position: position as [number, number, number],
    rotation: rotation as [number, number, number] | undefined,
    scale,
  }
}

export function parseIntersectionEnvironmentManifest(
  value: unknown,
  expectedIntersectionId?: string,
): IntersectionEnvironmentManifest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Intersection environment manifest must be an object')
  }
  const source = value as Record<string, unknown>
  const intersectionId = optionalString(source.intersectionId)
  if (source.schemaVersion !== 1 || !intersectionId) {
    throw new Error('Intersection environment manifest is incomplete')
  }
  if (expectedIntersectionId && intersectionId !== expectedIntersectionId) {
    throw new Error(`Environment ${intersectionId} does not match ${expectedIntersectionId}`)
  }
  return {
    ...(source as unknown as IntersectionEnvironmentManifest),
    buildingTilesetUrl: optionalString(source.buildingTilesetUrl),
    streetlight: parseStreetlight(source.streetlight),
    detailModel: parseDetailModel(source.detailModel),
  }
}

export async function loadIntersectionEnvironmentManifest(
  intersectionId: string,
): Promise<IntersectionEnvironmentManifest> {
  const value = await fetchJsonAsset<unknown>(
    `/intersections/v3/${intersectionId}/environment.json`,
    `路口 ${intersectionId} 环境清单`,
  )
  return parseIntersectionEnvironmentManifest(value, intersectionId)
}
