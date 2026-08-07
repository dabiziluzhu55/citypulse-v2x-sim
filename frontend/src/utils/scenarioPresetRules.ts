import { SCENARIO_MODE_OPTIONS } from '../constants/scenarioOptions.ts'
import { isBackendControlMode } from '../constants/simulationOptions.ts'
import type { CatalogScenarioPreset } from '../types/catalog.ts'

export interface IntersectionTargetEvent {
  intersection_ids: string[]
}

export interface ScenarioEventReconciliation<T extends IntersectionTargetEvent> {
  events: T[]
  removedIntersectionIds: string[]
  removedEventCount: number
}

export function scenarioPresetIntersectionIds(
  presetId: string,
  catalogPresets: CatalogScenarioPreset[] = [],
): string[] {
  const compatibilityPreset = SCENARIO_MODE_OPTIONS.find((item) => item.value === presetId)
  if (compatibilityPreset) return [...compatibilityPreset.intersectionIds]
  const catalogPreset = catalogPresets.find((item) => item.preset_id === presetId)
  return catalogPreset ? [...catalogPreset.intersection_ids] : []
}

export function controlModeSupportsScenario(controlMode: string, presetId: string): boolean {
  return isBackendControlMode(controlMode) && SCENARIO_MODE_OPTIONS.some(
    (item) => item.value === presetId,
  )
}

export function disturbanceTargetsOutsideScenario(
  events: IntersectionTargetEvent[],
  allowedIntersectionIds: string[],
): string[] {
  const allowed = new Set(allowedIntersectionIds)
  return [...new Set(events.flatMap((event) => (
    event.intersection_ids.filter((intersectionId) => !allowed.has(intersectionId))
  )))].sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))
}

export function reconcileEventsForScenario<T extends IntersectionTargetEvent>(
  events: T[],
  allowedIntersectionIds: string[],
): ScenarioEventReconciliation<T> {
  const allowed = new Set(allowedIntersectionIds)
  const removedIntersectionIds = disturbanceTargetsOutsideScenario(events, allowedIntersectionIds)
  let removedEventCount = 0
  const reconciled = events.flatMap((event) => {
    const intersectionIds = [...new Set(event.intersection_ids.filter((id) => allowed.has(id)))]
    if (intersectionIds.length === 0) {
      removedEventCount += 1
      return []
    }
    return [{ ...event, intersection_ids: intersectionIds }]
  })
  return { events: reconciled, removedIntersectionIds, removedEventCount }
}

export function formatMissingIntersectionMessage(intersectionIds: string[]): string {
  if (intersectionIds.length === 0) return ''
  return `后端仿真产物不完整，缺少：${intersectionIds
    .map((id) => id.replace(/^demo_/, '路口'))
    .join('、')}`
}
