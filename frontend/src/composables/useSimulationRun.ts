import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { controlRun, startRun } from '../api/simulation'
import { ACTIVE_RUN_ID_KEY } from '../constants/simulationOptions'
import type {
  ControlCommand,
  ControlRunResponse,
  StartRunRequest,
  StartRunResponse,
} from '../types/simulation'

function readStoredRunId(): string {
  return localStorage.getItem(ACTIVE_RUN_ID_KEY) ?? ''
}

function resolveInitialRunId(queryRunId: unknown): string {
  if (typeof queryRunId === 'string' && queryRunId.trim()) {
    return queryRunId.trim()
  }

  const stored = readStoredRunId()
  if (stored) {
    return stored
  }

  return import.meta.env.VITE_DEFAULT_RUN_ID ?? ''
}

export function useSimulationRun() {
  const route = useRoute()
  const router = useRouter()

  const runId = ref(resolveInitialRunId(route.query.run_id))
  const starting = ref(false)
  const controlling = ref(false)
  const startError = ref<string | null>(null)
  const controlError = ref<string | null>(null)
  const lastMessage = ref<string | null>(null)

  function persistRunId(nextRunId: string) {
    runId.value = nextRunId
    if (nextRunId) {
      localStorage.setItem(ACTIVE_RUN_ID_KEY, nextRunId)
    } else {
      localStorage.removeItem(ACTIVE_RUN_ID_KEY)
    }
  }

  function syncRunIdQuery(nextRunId: string) {
    const nextQuery = { ...route.query }
    if (nextRunId) {
      nextQuery.run_id = nextRunId
    } else {
      delete nextQuery.run_id
    }
    void router.replace({ query: nextQuery })
  }

  async function launchRun(payload: StartRunRequest): Promise<StartRunResponse | null> {
    starting.value = true
    startError.value = null

    try {
      const result = await startRun(payload)
      persistRunId(result.run_id)
      syncRunIdQuery(result.run_id)
      lastMessage.value = result.message
      return result
    } catch (err) {
      startError.value = err instanceof Error ? err.message : '启动仿真失败'
      return null
    } finally {
      starting.value = false
    }
  }

  async function sendControl(command: ControlCommand): Promise<ControlRunResponse | null> {
    if (!runId.value) {
      controlError.value = '请先启动仿真'
      return null
    }

    controlling.value = true
    controlError.value = null

    try {
      const result = await controlRun(runId.value, { command })
      lastMessage.value = `控制指令 ${command} 已执行，当前状态：${result.status}`
      return result
    } catch (err) {
      controlError.value = err instanceof Error ? err.message : '仿真控制失败'
      return null
    } finally {
      controlling.value = false
    }
  }

  watch(
    () => route.query.run_id,
    (queryRunId) => {
      if (typeof queryRunId === 'string' && queryRunId.trim() && queryRunId !== runId.value) {
        persistRunId(queryRunId.trim())
      }
    },
  )

  return {
    runId,
    starting,
    controlling,
    startError,
    controlError,
    lastMessage,
    launchRun,
    sendControl,
    persistRunId,
  }
}
