import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchHealth } from '../api/health'
import type { HealthResponse } from '../types/health'

const HEALTH_POLL_INTERVAL_MS = 10_000

export function useHealth() {
  const health = ref<HealthResponse | null>(null)
  const ready = ref(false)
  const error = ref<string | null>(null)
  const loading = ref(true)

  let timer: ReturnType<typeof setInterval> | null = null

  const statusLabel = computed(() => {
    if (error.value) {
      return '后端未连接'
    }
    if (!health.value) {
      return '检测中...'
    }
    return health.value.status === 'ok' ? 'SUMO 就绪' : 'SUMO 未就绪'
  })

  async function load() {
    try {
      const result = await fetchHealth()
      health.value = result.payload
      ready.value = result.ready
      error.value = null
    } catch (err) {
      health.value = null
      ready.value = false
      error.value = err instanceof Error ? err.message : '健康检查失败'
    } finally {
      loading.value = false
    }
  }

  onMounted(() => {
    void load()
    timer = setInterval(() => {
      void load()
    }, HEALTH_POLL_INTERVAL_MS)
  })

  onUnmounted(() => {
    if (timer !== null) {
      clearInterval(timer)
    }
  })

  return {
    health,
    ready,
    error,
    loading,
    statusLabel,
    refresh: load,
  }
}
