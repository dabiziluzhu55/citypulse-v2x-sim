import type { GeoJsonFeatureCollection } from '../../types/map'
import type { RoadCoordinateProjector, RoadSurfaceFeature } from '../roadGeometry'

export const MAX_SHOWCASE_MODEL_INSTANCES = 500

interface ShowcasePointFeature {
  type: 'Feature'
  properties: Record<string, unknown>
  geometry: { type: 'Point'; coordinates: number[] }
}

function projectCoordinates(
  value: unknown,
  projector: RoadCoordinateProjector,
): unknown {
  if (!Array.isArray(value)) throw new Error('GeoJSON coordinates must be arrays')
  if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
    if (!value.every((component) => typeof component === 'number' && Number.isFinite(component))) {
      throw new Error('GeoJSON coordinates must contain finite numbers')
    }
    const projected = projector(value)
    if (!Number.isFinite(projected[0]) || !Number.isFinite(projected[1])) {
      throw new Error('Projected GeoJSON coordinates must be finite')
    }
    return projected[2] == null
      ? [projected[0], projected[1]]
      : [projected[0], projected[1], projected[2]]
  }
  return value.map((child) => projectCoordinates(child, projector))
}

export function projectFeatureCollection(
  value: unknown,
  projector: RoadCoordinateProjector,
): GeoJsonFeatureCollection {
  if (!value || typeof value !== 'object') throw new Error('Expected a GeoJSON FeatureCollection')
  const collection = value as Record<string, unknown>
  if (collection.type !== 'FeatureCollection' || !Array.isArray(collection.features)) {
    throw new Error('Expected a GeoJSON FeatureCollection')
  }

  return {
    ...collection,
    type: 'FeatureCollection',
    features: collection.features.map((candidate) => {
      if (!candidate || typeof candidate !== 'object') throw new Error('Invalid GeoJSON feature')
      const feature = candidate as Record<string, unknown>
      const geometry = feature.geometry
      if (feature.type !== 'Feature' || !geometry || typeof geometry !== 'object') {
        throw new Error('Invalid GeoJSON feature')
      }
      const geometryRecord = geometry as Record<string, unknown>
      return {
        ...feature,
        type: 'Feature' as const,
        properties: feature.properties && typeof feature.properties === 'object'
          ? feature.properties as Record<string, unknown>
          : {},
        geometry: {
          ...geometryRecord,
          type: String(geometryRecord.type ?? ''),
          coordinates: projectCoordinates(geometryRecord.coordinates, projector),
        },
      }
    }),
  } as GeoJsonFeatureCollection
}

function samePosition(a: number[], b: number[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

function polygonCenter(surface: RoadSurfaceFeature): number[] | null {
  const ring = surface.geometry.coordinates[0]
    .filter((coordinate) => coordinate.length >= 2 && coordinate.every(Number.isFinite))
  if (ring.length < 3) return null
  const points = samePosition(ring[0], ring.at(-1)!) ? ring.slice(0, -1) : ring
  if (points.length < 3) return null
  const dimensions = Math.max(...points.map((point) => point.length))
  return Array.from({ length: dimensions }, (_, index) => (
    points.reduce((sum, point) => sum + Number(point[index] ?? 0), 0) / points.length
  ))
}

export function junctionSurfacesToPoints(
  surfaces: RoadSurfaceFeature[],
  limit = MAX_SHOWCASE_MODEL_INSTANCES,
): ShowcasePointFeature[] {
  return surfaces
    .map((surface) => {
      const coordinates = polygonCenter(surface)
      if (!coordinates) return null
      return {
        type: 'Feature' as const,
        properties: {
          ...surface.properties,
          model_type: 'junction-marker',
          text: `J${Number(surface.properties.junction_index ?? 0) + 1}`,
        } as Record<string, unknown>,
        geometry: { type: 'Point' as const, coordinates },
      }
    })
    .filter((feature): feature is NonNullable<typeof feature> => feature !== null)
    .sort((a, b) => (
      Number(a.properties.junction_index ?? 0) - Number(b.properties.junction_index ?? 0)
    ))
    .slice(0, Math.max(0, limit))
}
