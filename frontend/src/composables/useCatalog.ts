import { computed, onMounted, ref, type Ref } from 'vue'
import { fetchCatalog } from '../api/catalog'
import {
  resolveCatalogEventTypes,
  resolveCatalogPlaybackSpeeds,
} from '../constants/scenarioOptions'
import {
  DEFAULT_INTERSECTION_ID,
  resolveCatalogControlModes,
} from '../constants/simulationOptions'
import type { CatalogIntersection, CatalogResponse } from '../types/catalog'
import { catalogSupportsIntersection, findCatalogIntersection } from './catalogCapabilities'

export function useCatalog(intersectionId: Ref<string> | string = DEFAULT_INTERSECTION_ID) {
  const catalog = ref<CatalogResponse | null>(null)
  const loading = ref(true)
  const error = ref<string | null>(null)

  const intersection = computed<CatalogIntersection | null>(() => {
    if (!catalog.value) {
      return null
    }
    return findCatalogIntersection(
      catalog.value,
      typeof intersectionId === 'string' ? intersectionId : intersectionId.value,
    )
  })

  const supportedIntersectionIds = computed(() =>
    catalog.value?.intersections.map((item) => item.intersection_id) ?? [],
  )

  const isIntersectionSupported = computed(() => catalogSupportsIntersection(
    catalog.value,
    typeof intersectionId === 'string' ? intersectionId : intersectionId.value,
  ))

  const periods = computed<string[]>(() => intersection.value?.periods ?? [])
  const controlModes = computed<string[]>(() => resolveCatalogControlModes(catalog.value?.control_modes))
  const origins = computed(() => intersection.value?.origins ?? [])
  const scenarioPresets = computed(() => catalog.value?.scenario_presets ?? [])
  const playbackSpeeds = computed(() => resolveCatalogPlaybackSpeeds(catalog.value?.playback_speeds))
  const eventTypes = computed(() => resolveCatalogEventTypes(catalog.value?.event_types))

  async function load() {
    loading.value = true
    error.value = null
    try {
      catalog.value = await fetchCatalog()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载仿真目录失败'
    } finally {
      loading.value = false
    }
  }

  onMounted(load)

  return {
    catalog,
    intersection,
    periods,
    controlModes,
    origins,
    scenarioPresets,
    playbackSpeeds,
    eventTypes,
    supportedIntersectionIds,
    isIntersectionSupported,
    loading,
    error,
    refresh: load,
  }
}
