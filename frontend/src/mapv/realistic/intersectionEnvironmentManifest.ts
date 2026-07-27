import type { ShowcaseGeoJsonLayerUrls } from '../showcaseLayers/ShowcaseGeoJsonLayers.ts'

export interface IntersectionEnvironmentManifest {
  schemaVersion: 1
  intersectionId: string
  facilitiesUrl?: string
  vegetation?: {
    manifestUrl: string
    modelUrl: string
  }
  geojson?: ShowcaseGeoJsonLayerUrls
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
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
  return source as unknown as IntersectionEnvironmentManifest
}

export async function loadIntersectionEnvironmentManifest(
  intersectionId: string,
): Promise<IntersectionEnvironmentManifest> {
  const response = await fetch(`/intersections/v3/${intersectionId}/environment.json`)
  if (!response.ok) throw new Error(`Intersection environment returned HTTP ${response.status}`)
  return parseIntersectionEnvironmentManifest(await response.json(), intersectionId)
}
