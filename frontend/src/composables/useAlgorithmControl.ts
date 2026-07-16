import { computed, onMounted, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchAlgorithms, switchRunAlgorithm } from '../api/algorithm'
import type {
  AlgorithmComparisonRow,
  AlgorithmDefinition,
  AlgorithmMetrics,
  AlgorithmParameters,
} from '../types/algorithm'
import type { RunOverview } from '../types/overview'
import type { CollaborationStateSnapshot } from '../types/collaboration'

function extractMetrics(overview: RunOverview): AlgorithmMetrics {
  return {
    avg_speed: overview.avg_speed,
    avg_waiting_time: overview.avg_waiting_time,
    avg_queue_length: overview.avg_queue_length,
    congested_intersections: overview.congested_intersections,
  }
}

function buildComparisonRows(
  current: AlgorithmMetrics | null,
  baseline: AlgorithmMetrics | null,
): AlgorithmComparisonRow[] {
  const configs: Array<{
    key: keyof AlgorithmMetrics
    label: string
    unit: string
    higherIsBetter: boolean
  }> = [
    { key: 'avg_waiting_time', label: '平均等待时间', unit: 's', higherIsBetter: false },
    { key: 'avg_speed', label: '平均速度', unit: 'm/s', higherIsBetter: true },
    { key: 'avg_queue_length', label: '平均排队长度', unit: 'veh', higherIsBetter: false },
    { key: 'congested_intersections', label: '拥堵路口数', unit: '个', higherIsBetter: false },
  ]

  return configs.map((config) => {
    const currentValue = current?.[config.key] ?? null
    const baselineValue = baseline?.[config.key] ?? null
    let delta: number | null = null
    let improved: boolean | null = null

    if (currentValue != null && baselineValue != null && baselineValue !== 0) {
      delta = ((currentValue - baselineValue) / baselineValue) * 100
      improved = config.higherIsBetter ? delta > 0 : delta < 0
    }

    return {
      key: config.key,
      label: config.label,
      unit: config.unit,
      current: currentValue,
      baseline: baselineValue,
      delta,
      improved,
    }
  })
}

export function useAlgorithmControl(
  runId: Ref<string>,
  overview: Ref<RunOverview | null>,
  collaborationState: Ref<CollaborationStateSnapshot | null>,
) {
  const algorithms = ref<AlgorithmDefinition[]>([])
  const loadingList = ref(false)
  const switching = ref(false)
  const listError = ref<string | null>(null)
  const switchError = ref<string | null>(null)
  const switchMessage = ref<string | null>(null)
  const selectedAlgorithmId = ref('')
  const parameters = ref<AlgorithmParameters>({
    min_green: 10,
    max_green: 60,
  })
  const baselineMetrics = ref<AlgorithmMetrics | null>(null)
  const activeAlgorithmId = ref('')

  const currentAlgorithm = computed(() => {
    const id = activeAlgorithmId.value || overview.value?.algorithm || ''
    return algorithms.value.find((item) => item.algorithm_id === id) ?? null
  })

  const currentMetrics = computed(() =>
    overview.value ? extractMetrics(overview.value) : null,
  )

  const comparisonRows = computed(() =>
    buildComparisonRows(currentMetrics.value, baselineMetrics.value),
  )

  const inputState = computed(() => ({
    min_green: parameters.value.min_green,
    max_green: parameters.value.max_green,
    cloud_edge_enabled: overview.value?.cloud_edge_enabled ?? false,
    congested_intersections: overview.value?.congested_intersections ?? null,
    active_vehicle_count: overview.value?.active_vehicle_count ?? null,
  }))

  const outputActions = computed(() => {
    return (collaborationState.value?.edges ?? []).map((edge) => ({
      intersection_id: edge.intersection_id,
      action_type: edge.last_action.action_type,
      target_phase: edge.last_action.target_phase,
      duration: edge.last_action.duration,
      status: edge.status,
    }))
  })

  async function loadAlgorithms() {
    loadingList.value = true
    listError.value = null

    try {
      const response = await fetchAlgorithms()
      algorithms.value = response.algorithms
      if (!selectedAlgorithmId.value && response.algorithms.length > 0) {
        selectedAlgorithmId.value =
          overview.value?.algorithm ?? response.algorithms[0].algorithm_id
      }
    } catch (err) {
      listError.value = err instanceof Error ? err.message : '加载算法列表失败'
    } finally {
      loadingList.value = false
    }
  }

  async function applyAlgorithm() {
    if (!runId.value) {
      switchError.value = '请先启动仿真'
      return null
    }
    if (!selectedAlgorithmId.value) {
      switchError.value = '请选择算法'
      return null
    }

    switching.value = true
    switchError.value = null
    switchMessage.value = null

    try {
      const result = await switchRunAlgorithm(runId.value, {
        algorithm_id: selectedAlgorithmId.value,
        parameters: { ...parameters.value },
      })
      activeAlgorithmId.value = result.algorithm
      switchMessage.value = `算法已切换为 ${result.algorithm}（${result.status}）`
      ElMessage.success(switchMessage.value)
      return result
    } catch (err) {
      switchError.value = err instanceof Error ? err.message : '切换算法失败'
      return null
    } finally {
      switching.value = false
    }
  }

  watch(
    overview,
    (value) => {
      if (!value) {
        return
      }

      if (!selectedAlgorithmId.value) {
        selectedAlgorithmId.value = value.algorithm
      }

      if (value.algorithm === 'fixed_time') {
        baselineMetrics.value = extractMetrics(value)
      }
    },
    { immediate: true },
  )

  watch(
    () => overview.value?.algorithm,
    (algorithm) => {
      if (algorithm) {
        activeAlgorithmId.value = algorithm
        if (!selectedAlgorithmId.value) {
          selectedAlgorithmId.value = algorithm
        }
      }
    },
    { immediate: true },
  )

  onMounted(() => {
    void loadAlgorithms()
  })

  return {
    algorithms,
    loadingList,
    switching,
    listError,
    switchError,
    switchMessage,
    selectedAlgorithmId,
    parameters,
    currentAlgorithm,
    currentMetrics,
    baselineMetrics,
    comparisonRows,
    inputState,
    outputActions,
    loadAlgorithms,
    applyAlgorithm,
  }
}
