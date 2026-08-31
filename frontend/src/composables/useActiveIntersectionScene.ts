import { computed, ref } from 'vue'
import { DEFAULT_INTERSECTION_ID } from '../constants/simulationOptions.ts'

const activeIntersectionId = ref(DEFAULT_INTERSECTION_ID)
const sceneStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const sceneError = ref<string | null>(null)
const selectionRevision = ref(0)
const committedIntersectionId = ref<string | null>(null)

export interface IntersectionSceneSelectionState {
  requestedIntersectionId: string
  committedIntersectionId: string | null
  switching: boolean
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string | null
}

export function useActiveIntersectionScene() {
  const hasActiveIntersection = computed(() => Boolean(activeIntersectionId.value))
  const selectionState = computed<IntersectionSceneSelectionState>(() => ({
    requestedIntersectionId: activeIntersectionId.value,
    committedIntersectionId: committedIntersectionId.value,
    switching: Boolean(
      committedIntersectionId.value
      && committedIntersectionId.value !== activeIntersectionId.value
    ),
    status: sceneStatus.value,
    error: sceneError.value,
  }))

  function selectIntersection(intersectionId: string): void {
    if (!intersectionId) return
    if (intersectionId === activeIntersectionId.value) return
    activeIntersectionId.value = intersectionId
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

  function restoreCommittedIntersection(message: string): string | null {
    const committed = committedIntersectionId.value
    if (committed) activeIntersectionId.value = committed
    sceneStatus.value = committed ? 'ready' : 'error'
    sceneError.value = message
    return committed
  }

  return {
    activeIntersectionId,
    hasActiveIntersection,
    sceneStatus,
    sceneError,
    selectionRevision,
    committedIntersectionId,
    selectionState,
    selectIntersection,
    setSceneLoading,
    setSceneReady,
    setSceneError,
    restoreCommittedIntersection,
  }
}
