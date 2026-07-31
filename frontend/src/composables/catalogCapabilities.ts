import type {
  CatalogIntersection,
  CatalogResponse,
  CatalogScenarioPreset,
} from '../types/catalog'

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

export function findCatalogScenarioPreset(
  catalog: CatalogResponse | null,
  presetId: string,
): CatalogScenarioPreset | null {
  return catalog?.scenario_presets.find((item) => item.preset_id === presetId) ?? null
}

export function missingPresetIntersectionIds(
  catalog: CatalogResponse | null,
  presetId: string,
): string[] {
  const preset = findCatalogScenarioPreset(catalog, presetId)
  if (!preset) return []
  const available = new Set(catalog?.intersections.map((item) => item.intersection_id) ?? [])
  return preset.intersection_ids.filter((intersectionId) => !available.has(intersectionId))
}

export function catalogSupportsScenarioPreset(
  catalog: CatalogResponse | null,
  presetId: string,
): boolean {
  return !!findCatalogScenarioPreset(catalog, presetId)
    && missingPresetIntersectionIds(catalog, presetId).length === 0
}

export function catalogSupportsScenarioPresetForIntersection(
  catalog: CatalogResponse | null,
  presetId: string,
  intersectionId: string,
): boolean {
  const preset = findCatalogScenarioPreset(catalog, presetId)
  return catalogSupportsScenarioPreset(catalog, presetId)
    && !!preset?.intersection_ids.includes(intersectionId)
}

export function findRunnableScenarioPreset(
  catalog: CatalogResponse | null,
  intersectionId: string,
): CatalogScenarioPreset | null {
  return catalog?.scenario_presets.find((preset) => (
    catalogSupportsScenarioPresetForIntersection(
      catalog,
      preset.preset_id,
      intersectionId,
    )
  )) ?? null
}
