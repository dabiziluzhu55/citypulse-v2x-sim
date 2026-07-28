import type { CatalogIntersection, CatalogResponse } from '../types/catalog'

export function findCatalogIntersection(
  catalog: CatalogResponse | null,
  intersectionId: string,
): CatalogIntersection | null {
  return catalog?.intersections.find(
    (item) => item.intersection_id === intersectionId,
  ) ?? null
}

export function catalogSupportsIntersection(
  catalog: CatalogResponse | null,
  intersectionId: string,
): boolean {
  return findCatalogIntersection(catalog, intersectionId) !== null
}

export function requireSimulatableIntersection(
  intersection: CatalogIntersection | null,
): CatalogIntersection {
  if (!intersection) {
    throw new Error('当前路口仅支持高精度查看，尚未接入真实仿真路网')
  }
  return intersection
}
