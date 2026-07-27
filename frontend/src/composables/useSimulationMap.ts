import { onMounted, ref, watch, type Ref } from 'vue'
import { fetchMapGeoJson } from '../api/maps'
import { DEFAULT_INTERSECTION_ID } from '../constants/simulationOptions'
import type { MapGeoJsonResponse } from '../types/map'

export function useSimulationMap(
  intersectionId: Ref<string> | string = DEFAULT_INTERSECTION_ID,
  radiusM = 600,
  enabled: Ref<boolean> | boolean = true,
) {
  const geojson = ref<MapGeoJsonResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  function resolveId(): string {
    return typeof intersectionId === 'string' ? intersectionId : intersectionId.value
  }

  function isEnabled(): boolean {
    return typeof enabled === 'boolean' ? enabled : enabled.value
  }

  async function load() {
    const id = resolveId()
    if (!id || !isEnabled()) {
      geojson.value = null
      loading.value = false
      return
    }

    loading.value = true
    error.value = null
    try {
      geojson.value = await fetchMapGeoJson(id, radiusM)
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载路网地图失败'
      geojson.value = null
    } finally {
      loading.value = false
    }
  }

  onMounted(load)

  if (typeof intersectionId !== 'string' || typeof enabled !== 'boolean') {
    const dependencies = [
      ...(typeof intersectionId === 'string' ? [] : [intersectionId]),
      ...(typeof enabled === 'boolean' ? [] : [enabled]),
    ]
    watch(dependencies, load)
  }

  return {
    geojson,
    loading,
    error,
    refresh: load,
  }
}
