import { computed, ref } from 'vue'
import { DEFAULT_INTERSECTION_ID } from '../constants/simulationOptions.ts'

const activeIntersectionId = ref(DEFAULT_INTERSECTION_ID)
const sceneStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const sceneError = ref<string | null>(null)
const selectionRevision = ref(0)
const committedIntersectionId = ref<string | null>(null)

export function useActiveIntersectionScene() {
  const hasActiveIntersection = computed(() => Boolean(activeIntersectionId.value))

  function selectIntersection(intersectionId: string): void {
    if (!intersectionId) return
    if (intersectionId !== activeIntersectionId.value) activeIntersectionId.value = intersectionId
    selectionRevision.value += 1
    sceneStatus.value = 'idle'
    sceneError.value = null
  }

  function setSceneLoading(): void {
    sceneStatus.value = 'loading'
    sceneError.value = null
  }

  function setSceneReady(intersectionId = activeIntersectionId.value): void {
    committedIntersectionId.value = intersectionId
    sceneStatus.value = 'ready'
    sceneError.value = null
  }

  function setSceneError(message: string): void {
    sceneStatus.value = 'error'
    sceneError.value = message
  }

  return {
    activeIntersectionId,
    hasActiveIntersection,
    sceneStatus,
    sceneError,
    selectionRevision,
    committedIntersectionId,
    selectIntersection,
    setSceneLoading,
    setSceneReady,
    setSceneError,
  }
}
