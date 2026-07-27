import { computed, onMounted, ref, type Ref } from 'vue'
import { fetchCatalog } from '../api/catalog'
import { DEFAULT_INTERSECTION_ID } from '../constants/simulationOptions'
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
  const controlModes = computed<string[]>(() => catalog.value?.control_modes ?? ['fixed'])
  const origins = computed(() => intersection.value?.origins ?? [])
  const flowMultiplierRange = computed(
    () => catalog.value?.flow_multiplier ?? { min: 0.1, max: 5 },
  )

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
    flowMultiplierRange,
    supportedIntersectionIds,
    isIntersectionSupported,
    loading,
    error,
    refresh: load,
  }
}
