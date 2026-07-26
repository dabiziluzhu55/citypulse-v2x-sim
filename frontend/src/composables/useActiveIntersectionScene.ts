import { computed, ref } from 'vue'
import { DEFAULT_INTERSECTION_ID } from '../constants/simulationOptions'

const activeIntersectionId = ref(DEFAULT_INTERSECTION_ID)
const sceneStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const sceneError = ref<string | null>(null)

export function useActiveIntersectionScene() {
  const hasActiveIntersection = computed(() => Boolean(activeIntersectionId.value))

  function selectIntersection(intersectionId: string): void {
    if (!intersectionId || intersectionId === activeIntersectionId.value) return
    activeIntersectionId.value = intersectionId
    sceneStatus.value = 'idle'
    sceneError.value = null
  }

  function setSceneLoading(): void {
    sceneStatus.value = 'loading'
    sceneError.value = null
  }

  function setSceneReady(): void {
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
    selectIntersection,
    setSceneLoading,
    setSceneReady,
    setSceneError,
  }
}
