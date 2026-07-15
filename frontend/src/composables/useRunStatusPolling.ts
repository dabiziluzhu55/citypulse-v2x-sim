import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { fetchRunStatus } from '../api/simulation'
import { STATUS_POLL_INTERVAL_MS } from '../constants/simulationOptions'
import type { RunLifecycleStatus, RunStatus } from '../types/simulation'

const TERMINAL_STATUSES: RunLifecycleStatus[] = ['stopped', 'idle', 'error']

function shouldPoll(runId: string, status: RunLifecycleStatus | null) {
  if (!runId) {
    return false
  }
  if (!status) {
    return true
  }
  return !TERMINAL_STATUSES.includes(status)
}

export function useRunStatusPolling(runId: Ref<string>) {
  const status = ref<RunStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  let timerId: ReturnType<typeof setInterval> | null = null
  let requestVersion = 0

  async function pollOnce() {
    if (!runId.value) {
      status.value = null
      error.value = null
      loading.value = false
      return
    }

    const currentVersion = ++requestVersion
    loading.value = true

    try {
      const nextStatus = await fetchRunStatus(runId.value)
      if (currentVersion !== requestVersion) {
        return
      }
      status.value = nextStatus
      error.value = null
    } catch (err) {
      if (currentVersion !== requestVersion) {
        return
      }
      error.value = err instanceof Error ? err.message : '获取仿真状态失败'
    } finally {
      if (currentVersion === requestVersion) {
        loading.value = false
      }
    }
  }

  function stopPolling() {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
  }

  function startPolling() {
    stopPolling()
    void pollOnce()

    if (!shouldPoll(runId.value, status.value?.status ?? null)) {
      return
    }

    timerId = setInterval(() => {
      if (!shouldPoll(runId.value, status.value?.status ?? null)) {
        stopPolling()
        return
      }
      void pollOnce()
    }, STATUS_POLL_INTERVAL_MS)
  }

  function refresh() {
    void pollOnce().then(() => {
      if (shouldPoll(runId.value, status.value?.status ?? null)) {
        startPolling()
      }
    })
  }

  onMounted(startPolling)
  onUnmounted(stopPolling)

  watch(runId, () => {
    status.value = null
    startPolling()
  })

  watch(
    () => status.value?.status,
    (nextStatus) => {
      if (nextStatus && TERMINAL_STATUSES.includes(nextStatus)) {
        stopPolling()
      } else if (runId.value) {
        startPolling()
      }
    },
  )

  return {
    status,
    loading,
    error,
    refresh,
  }
}
