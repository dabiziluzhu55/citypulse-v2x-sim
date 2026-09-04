import { readonly, ref } from 'vue'

const activeEventId = ref<string | null>(null)
const activeEventLabel = ref<string | null>(null)

export function useCopilotContext() {
  function selectCopilotEvent(eventId: string | null | undefined, label?: string | null) {
    const normalizedEventId = eventId?.trim() || null
    activeEventId.value = normalizedEventId
    activeEventLabel.value = normalizedEventId ? label?.trim() || null : null
  }

  function clearCopilotEvent(expectedEventId?: string | null) {
    if (expectedEventId && activeEventId.value !== expectedEventId) return
    activeEventId.value = null
    activeEventLabel.value = null
  }

  return {
    activeEventId: readonly(activeEventId),
    activeEventLabel: readonly(activeEventLabel),
    selectCopilotEvent,
    clearCopilotEvent,
  }
}
